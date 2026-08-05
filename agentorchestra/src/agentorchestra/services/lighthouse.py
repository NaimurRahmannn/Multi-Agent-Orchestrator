from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from agentorchestra.config import Settings, ensure_runtime_directories, get_settings
from agentorchestra.models import EditRequest
from agentorchestra.seo_models import (
    LighthouseAuditItem,
    LighthouseAuditStatus,
    LighthouseSEOResult,
)
from agentorchestra.services.preview_server import serve_site
from agentorchestra.services.workspace import (
    read_file,
    validate_site_structure,
    validate_staged_site,
)
from agentorchestra.workspace_models import WorkspaceHandle

SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]
PreviewFactory = Callable[[Path], AbstractContextManager[str]]
Clock = Callable[[], float]
IdFactory = Callable[[], str]


def run_lighthouse_seo(
    workspace: WorkspaceHandle,
    target_page: str,
    *,
    settings: Settings | None = None,
    subprocess_runner: SubprocessRunner = subprocess.run,
    preview_factory: PreviewFactory = serve_site,
    clock: Clock = time.perf_counter,
    report_id_factory: IdFactory = lambda: uuid.uuid4().hex,
    timeout_seconds: int = 120,
) -> LighthouseSEOResult:
    """Run only the Lighthouse SEO category against one staged target page."""
    resolved = settings or get_settings()
    validated_target = _validate_target_page(target_page)
    try:
        validate_staged_site(workspace)
        read_file(
            workspace,
            file=validated_target,
            start_line=1,
            end_line=1,
            allowed_files=(validated_target,),
        )
    except Exception:
        return _failed(workspace.run_id, validated_target, 0.0, "Staged audit target is invalid.")
    return _run_site_lighthouse(
        site_root=workspace.path,
        run_id=workspace.run_id,
        target_page=validated_target,
        settings=resolved,
        subprocess_runner=subprocess_runner,
        preview_factory=preview_factory,
        clock=clock,
        report_id_factory=report_id_factory,
        timeout_seconds=timeout_seconds,
    )


def run_working_lighthouse_seo(
    target_page: str,
    *,
    settings: Settings | None = None,
    subprocess_runner: SubprocessRunner = subprocess.run,
    preview_factory: PreviewFactory = serve_site,
    clock: Clock = time.perf_counter,
    report_id_factory: IdFactory = lambda: uuid.uuid4().hex,
    timeout_seconds: int = 120,
) -> LighthouseSEOResult:
    """Run the same SEO-only audit against the protected working site without modifying it."""
    resolved = settings or get_settings()
    target = _validate_target_page(target_page)
    run_id = f"working-{_safe_id(report_id_factory())}"
    try:
        validate_site_structure(resolved.working_site_dir)
        page = resolved.working_site_dir / target
        if page.is_symlink() or not page.is_file():
            raise ValueError
    except Exception:
        return _failed(run_id, target, 0.0, "Working audit target is invalid.")
    return _run_site_lighthouse(
        site_root=resolved.working_site_dir,
        run_id=run_id,
        target_page=target,
        settings=resolved,
        subprocess_runner=subprocess_runner,
        preview_factory=preview_factory,
        clock=clock,
        report_id_factory=report_id_factory,
        timeout_seconds=timeout_seconds,
    )


def _run_site_lighthouse(
    *,
    site_root: Path,
    run_id: str,
    target_page: str,
    settings: Settings,
    subprocess_runner: SubprocessRunner,
    preview_factory: PreviewFactory,
    clock: Clock,
    report_id_factory: IdFactory,
    timeout_seconds: int,
) -> LighthouseSEOResult:
    started = clock()
    ensure_runtime_directories(settings)
    report_name = f"seo-{run_id}-{_safe_id(report_id_factory())}.json"
    output_path = settings.lighthouse_report_dir / report_name
    relative_report = output_path.relative_to(settings.project_root).as_posix()
    npx = _resolve_npx_executable()
    try:
        with preview_factory(site_root) as base_url:
            command = [
                npx,
                "--no-install",
                "lighthouse",
                f"{base_url}/{target_page}",
                "--only-categories=seo",
                "--output=json",
                f"--output-path={output_path}",
                "--chrome-flags=--headless=new",
            ]
            completed = subprocess_runner(
                command,
                cwd=settings.project_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
    except FileNotFoundError:
        return _failed(
            run_id, target_page, _elapsed_ms(started, clock), "Lighthouse is unavailable."
        )
    except subprocess.TimeoutExpired:
        return _failed(
            run_id, target_page, _elapsed_ms(started, clock), "Lighthouse SEO audit timed out."
        )
    except Exception:
        return _failed(
            run_id,
            target_page,
            _elapsed_ms(started, clock),
            "Lighthouse SEO audit could not start safely.",
        )
    latency_ms = _elapsed_ms(started, clock)
    try:
        if completed.returncode != 0 and not output_path.is_file():
            return _failed(
                run_id,
                target_page,
                latency_ms,
                "Lighthouse SEO audit failed.",
            )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return normalize_lighthouse_seo_report(
            payload,
            run_id=run_id,
            target_page=target_page,
            report_path=relative_report,
            latency_ms=latency_ms,
        )
    except Exception:
        return _failed(
            run_id,
            target_page,
            latency_ms,
            "Lighthouse report was malformed or missing SEO evidence.",
        )


def normalize_lighthouse_seo_report(
    payload: dict[str, Any],
    *,
    run_id: str,
    target_page: str,
    report_path: str,
    latency_ms: float,
) -> LighthouseSEOResult:
    """Normalize the SEO category and only its referenced audits."""
    category = payload["categories"]["seo"]
    raw_score = category["score"]
    if not isinstance(raw_score, int | float) or isinstance(raw_score, bool):
        raise ValueError("SEO category score is invalid.")
    score = round(float(raw_score) * 100)
    if not 0 <= score <= 100:
        raise ValueError("SEO category score is outside the supported range.")
    all_audits = payload["audits"]
    references = category["auditRefs"]
    items: list[LighthouseAuditItem] = []
    for reference in references:
        audit_id = reference["id"]
        raw = all_audits[audit_id]
        display_mode = raw.get("scoreDisplayMode")
        raw_audit_score = raw.get("score")
        status = _audit_status(display_mode, raw_audit_score)
        normalized_score = (
            round(float(raw_audit_score) * 100)
            if isinstance(raw_audit_score, int | float) and not isinstance(raw_audit_score, bool)
            else None
        )
        display_value = raw.get("displayValue")
        items.append(
            LighthouseAuditItem(
                audit_id=audit_id,
                title=raw["title"],
                status=status,
                score=normalized_score,
                display_value=(str(display_value)[:500] if display_value else None),
            )
        )
    items.sort(key=lambda item: item.audit_id)
    return LighthouseSEOResult(
        status="succeeded",
        run_id=run_id,
        target_page=target_page,
        score=score,
        audits=items,
        failed_audit_ids=sorted(
            item.audit_id for item in items if item.status is LighthouseAuditStatus.FAILED
        ),
        report_path=report_path,
        latency_ms=float(latency_ms),
        error=None,
    )


def _audit_status(display_mode: object, score: object) -> LighthouseAuditStatus:
    if display_mode == "notApplicable":
        return LighthouseAuditStatus.NOT_APPLICABLE
    if display_mode in {"informative", "manual"} or score is None:
        return LighthouseAuditStatus.INFORMATIVE
    if isinstance(score, int | float) and not isinstance(score, bool) and float(score) >= 1:
        return LighthouseAuditStatus.PASSED
    return LighthouseAuditStatus.FAILED


def _failed(run_id: str, target_page: str, latency_ms: float, error: str) -> LighthouseSEOResult:
    return LighthouseSEOResult(
        status="failed",
        run_id=run_id,
        target_page=target_page,
        latency_ms=float(max(0.0, latency_ms)),
        error=error,
    )


def _validate_target_page(value: str) -> str:
    return EditRequest(target_page=value, instruction="Validate Lighthouse target.").target_page


def _resolve_npx_executable() -> str:
    resolved = shutil.which("npx")
    if resolved:
        return resolved
    return "npx"


def _safe_id(value: str) -> str:
    if (
        not value
        or len(value) > 64
        or not all(char.isalnum() or char in {"-", "_"} for char in value)
    ):
        raise ValueError("Generated Lighthouse report identifier is invalid.")
    return value


def _elapsed_ms(started: float, clock: Clock) -> float:
    return float(max(0.0, (clock() - started) * 1000))
