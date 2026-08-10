from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agentorchestra.config import Settings, ensure_runtime_directories, get_settings
from agentorchestra.exceptions import ScreenshotSafetyError
from agentorchestra.models import EditRequest
from agentorchestra.path_safety import redact_absolute_path_text, redact_secret_like_text
from agentorchestra.screenshot_models import (
    ScreenshotArtifact,
    ScreenshotKind,
    ScreenshotStatus,
    _validate_run_id,
)
from agentorchestra.services.preview_server import serve_site
from agentorchestra.services.workspace import validate_site_structure

VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900
NAVIGATION_TIMEOUT_MS = 15_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

Clock = Callable[[], float]
PlaywrightFactory = Callable[[], AbstractContextManager[Any]]
PreviewFactory = Callable[[Path], AbstractContextManager[str]]


def capture_page_screenshot(
    *,
    settings: Settings | None = None,
    site_root: Path,
    target_page: str,
    run_id: str,
    kind: ScreenshotKind,
    source_site_digest: str,
    clock: Clock = time.perf_counter,
    playwright_factory: PlaywrightFactory | None = None,
    preview_factory: PreviewFactory = serve_site,
) -> ScreenshotArtifact:
    """Capture one verified local-only screenshot without mutating the served site."""
    resolved = settings or get_settings()
    try:
        kind = ScreenshotKind(kind)
    except ValueError as exc:
        raise ScreenshotSafetyError("Screenshot kind is invalid.") from exc
    target = _validate_capture_inputs(
        settings=resolved,
        site_root=site_root,
        target_page=target_page,
        run_id=run_id,
    )
    if re.fullmatch(r"[0-9a-f]{64}", source_site_digest) is None:
        raise ScreenshotSafetyError("Screenshot source digest is invalid.")
    output_path = _build_output_path(resolved, run_id, kind, target_page)
    relative_path = output_path.relative_to(resolved.project_root.resolve()).as_posix()
    started = clock()
    page = context = browser = None
    factory = playwright_factory or _sync_playwright_factory
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with preview_factory(target) as base_url, factory() as playwright:
            try:
                browser = _launch_chromium(playwright.chromium)
                context = browser.new_context(
                    viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
                )
                page = context.new_page()
                origin = _loopback_origin(base_url)
                page.route("**/*", lambda route: _route_local_only(route, origin))
                page.goto(
                    f"{base_url.rstrip('/')}/{target_page}",
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                page.wait_for_load_state("load", timeout=5_000)
                page.screenshot(path=str(output_path), full_page=True)
            finally:
                _close_safely(page)
                _close_safely(context)
                _close_safely(browser)
                page = context = browser = None
        _verify_png(output_path)
        return ScreenshotArtifact(
            kind=kind,
            status=ScreenshotStatus.SUCCEEDED,
            run_id=run_id,
            target_page=target_page,
            source_site_digest=source_site_digest,
            relative_path=relative_path,
            viewport_width=VIEWPORT_WIDTH,
            viewport_height=VIEWPORT_HEIGHT,
            full_page=True,
            latency_ms=_elapsed_ms(started, clock),
        )
    except ScreenshotSafetyError:
        _cleanup_partial(output_path)
        raise
    except Exception as exc:
        _cleanup_partial(output_path)
        return ScreenshotArtifact(
            kind=kind,
            status=ScreenshotStatus.FAILED,
            run_id=run_id,
            target_page=target_page,
            source_site_digest=source_site_digest,
            viewport_width=VIEWPORT_WIDTH,
            viewport_height=VIEWPORT_HEIGHT,
            full_page=True,
            latency_ms=_elapsed_ms(started, clock),
            error=_safe_error(exc, resolved),
        )
    finally:
        _close_safely(page)
        _close_safely(context)
        _close_safely(browser)


def _validate_capture_inputs(
    *, settings: Settings, site_root: Path, target_page: str, run_id: str
) -> Path:
    try:
        _validate_run_id(run_id)
        target_page = EditRequest(
            target_page=target_page,
            instruction="Validate screenshot target.",
        ).target_page
        lexical_root = Path(os.path.abspath(site_root))
        if lexical_root.is_symlink():
            raise ScreenshotSafetyError("Screenshot site root must not be a symlink.")
        validate_site_structure(lexical_root)
        root = lexical_root.resolve(strict=True)
        project_root = settings.project_root.resolve(strict=True)
        root.relative_to(project_root)
        working = settings.working_site_dir.resolve(strict=True)
        staging = settings.staging_root_dir.resolve(strict=False)
        trusted = root == working or (root.parent == staging and root.name == run_id)
        if not trusted:
            raise ScreenshotSafetyError("Screenshot site root is not application-owned.")
        page = root / target_page
        if page.is_symlink() or not page.is_file() or page.resolve(strict=True).parent != root:
            raise ScreenshotSafetyError("Screenshot target page is invalid.")
        return root
    except ScreenshotSafetyError:
        raise
    except Exception as exc:
        raise ScreenshotSafetyError("Screenshot capture input failed safety validation.") from exc


def _build_output_path(
    settings: Settings, run_id: str, kind: ScreenshotKind, target_page: str
) -> Path:
    ensure_runtime_directories(settings)
    report_root = settings.screenshot_report_dir
    if report_root.is_symlink():
        raise ScreenshotSafetyError("Screenshot report root must not be a symlink.")
    run_dir = report_root / run_id
    if run_dir.is_symlink():
        raise ScreenshotSafetyError("Screenshot run directory must not be a symlink.")
    if run_dir.exists() and not run_dir.is_dir():
        raise ScreenshotSafetyError("Screenshot run path is not a directory.")
    page_stem = Path(target_page).stem
    kind_name = kind.value.replace("_", "-")
    output = run_dir / f"{kind_name}-{page_stem}.png"
    if output.is_symlink():
        raise ScreenshotSafetyError("Screenshot output must not be a symlink.")
    try:
        output.resolve(strict=False).relative_to(report_root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ScreenshotSafetyError("Screenshot output escaped the report root.") from exc
    return output


def _sync_playwright_factory() -> AbstractContextManager[Any]:
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _launch_chromium(chromium: Any) -> Any:
    bundled = Path(chromium.executable_path)
    if bundled.is_file():
        return chromium.launch(headless=True)
    configured = os.environ.get("CHROME_PATH", "").strip()
    system = shutil.which("google-chrome") or shutil.which("chromium")
    fallback = Path(configured) if configured else Path(system) if system else None
    if fallback is not None and fallback.is_file():
        return chromium.launch(headless=True, executable_path=str(fallback))
    raise RuntimeError(
        "Chromium browser is unavailable. Install Playwright Chromium or set CHROME_PATH."
    )


def _loopback_origin(base_url: str) -> tuple[str, str, int]:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ScreenshotSafetyError("Preview server did not provide a loopback HTTP origin.")
    if parsed.port is None:
        raise ScreenshotSafetyError("Preview server did not provide an ephemeral port.")
    return parsed.scheme, parsed.hostname, parsed.port


def _route_local_only(route: Any, origin: tuple[str, str, int]) -> None:
    parsed = urlsplit(route.request.url)
    if parsed.scheme in {"http", "https"}:
        request_origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        if request_origin != origin:
            route.abort()
            return
    route.continue_()


def _verify_png(output_path: Path) -> None:
    if output_path.is_symlink() or not output_path.is_file():
        raise RuntimeError("Screenshot output was not a regular file.")
    with output_path.open("rb") as image:
        if image.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
            raise RuntimeError("Screenshot output was not a valid PNG.")
        if not image.read(1):
            raise RuntimeError("Screenshot output was empty.")


def _cleanup_partial(output_path: Path) -> None:
    try:
        if output_path.is_symlink() or output_path.is_file():
            output_path.unlink(missing_ok=True)
        parent = output_path.parent
        if parent.is_dir() and not parent.is_symlink() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _close_safely(resource: Any) -> None:
    if resource is None:
        return
    with suppress(Exception):
        resource.close()


def _safe_error(exc: Exception, settings: Settings) -> str:
    clean = str(exc).replace("\n", " ").strip()
    clean = clean.replace(str(settings.project_root), "[project]")
    for secret in settings.groq_api_key_values:
        clean = clean.replace(secret, "[redacted]")
    clean = redact_absolute_path_text(clean)
    clean = redact_secret_like_text(clean[:700] or "Screenshot capture failed.")
    return clean.encode("ascii", errors="replace").decode("ascii")


def _elapsed_ms(started: float, clock: Clock) -> float:
    return float(max(0.0, (clock() - started) * 1000))
