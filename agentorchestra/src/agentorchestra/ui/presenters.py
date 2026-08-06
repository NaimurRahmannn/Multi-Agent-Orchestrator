from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentorchestra.config import Settings
from agentorchestra.path_safety import (
    redact_absolute_path_text,
    redact_secret_like_text,
    validate_relative_site_path,
)
from agentorchestra.pipeline_models import EditOutcomeStatus, EditRunReport
from agentorchestra.screenshot_models import ScreenshotArtifact, ScreenshotStatus
from agentorchestra.services.screenshots import PNG_SIGNATURE


def build_timeline_rows(report: EditRunReport) -> list[dict[str, object]]:
    return [
        {
            "sequence": event.sequence,
            "stage": event.stage.value,
            "status": event.status.value,
            "duration_ms": round(event.duration_ms, 1),
            "message": event.message,
        }
        for event in report.timeline.events
    ]


def build_specialist_rows(report: EditRunReport) -> list[dict[str, object]]:
    if report.specialist_report is None:
        return []
    return [
        {
            "specialist": result.specialist.value,
            "assignment": result.assignment,
            "status": result.status.value,
            "completion": result.completion.summary if result.completion else "unavailable",
            "applied": result.applied_patch_count,
            "rejected": result.rejected_patch_count,
            "changed_files": ", ".join(result.changed_files) or "none",
            "latency_ms": round(result.latency_ms, 1),
            "tokens": _usage_text(result.token_usage),
        }
        for result in report.specialist_report.results
    ]


def build_patch_rows(report: EditRunReport) -> list[dict[str, object]]:
    if report.specialist_report is None:
        return []
    return [
        {
            "specialist": result.specialist.value,
            "file": patch.file,
            "status": patch.status.value,
            "reason": patch.rejection_reason.value if patch.rejection_reason else "",
            "match_count": patch.match_count if patch.match_count is not None else "unavailable",
            "replacements": patch.replacements,
            "summary": patch.summary,
        }
        for result in report.specialist_report.results
        for patch in result.patch_results
    ]


def build_qa_rows(report: EditRunReport) -> list[dict[str, str]]:
    if report.qa_run is None:
        return []
    return [
        {
            "criterion": item.criterion,
            "status": item.status.value,
            "evidence": item.evidence,
        }
        for item in report.qa_run.result.criteria_results
    ]


def build_metric_cards(report: EditRunReport) -> dict[str, str]:
    metrics = report.metrics
    if metrics is None:
        return {}
    return {
        "Total latency": f"{metrics.total_latency_ms:.1f} ms",
        "Manager": f"{metrics.manager_latency_ms:.1f} ms",
        "Specialists": f"{metrics.specialist_latency_ms:.1f} ms",
        "Lighthouse": f"{metrics.lighthouse_latency_ms:.1f} ms",
        "Screenshots": f"{metrics.screenshot_latency_ms:.1f} ms",
        "QA": f"{metrics.qa_latency_ms:.1f} ms",
        "Promotion": f"{metrics.promotion_latency_ms:.1f} ms",
        "Patches": f"{metrics.applied_patch_count} applied / {metrics.rejected_patch_count} rejected",
        "Diff": f"{metrics.changed_file_count} files, +{metrics.added_lines}/-{metrics.removed_lines}",
        "Tokens": (
            str(metrics.known_total_tokens)
            if metrics.known_total_tokens is not None
            else "unavailable"
        ),
        "Token metadata": "complete" if metrics.token_usage_complete else "incomplete",
    }


def resolve_screenshot_for_display(
    settings: Settings, artifact: ScreenshotArtifact
) -> bytes | None:
    if artifact.status is not ScreenshotStatus.SUCCEEDED or artifact.relative_path is None:
        return None
    relative = validate_relative_site_path(artifact.relative_path)
    if not relative.startswith("reports/screenshots/"):
        return None
    root = settings.screenshot_report_dir.resolve(strict=True)
    candidate = settings.project_root / relative
    if candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError):
        return None
    if resolved.suffix.lower() != ".png":
        return None
    content = resolved.read_bytes()
    return content if content.startswith(PNG_SIGNATURE) and len(content) > len(PNG_SIGNATURE) else None


def sanitized_report(report: EditRunReport, settings: Settings) -> EditRunReport:
    payload = _sanitize_value(report.model_dump(mode="python"), settings)
    return EditRunReport.model_validate(payload)


def report_download_bytes(report: EditRunReport, settings: Settings) -> bytes:
    safe = sanitized_report(report, settings)
    return json.dumps(safe.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8")


def diff_download_bytes(report: EditRunReport) -> bytes:
    diff = report.reviewed_diff
    return (diff.combined_diff if diff is not None else "").encode("utf-8")


def download_filenames(report: EditRunReport) -> tuple[str, str]:
    run_id = report.run_id or "no-run"
    stem = Path(report.request.target_page).stem
    return f"agentorchestra-{run_id}-{stem}.json", f"agentorchestra-{run_id}-{stem}.diff"


def outcome_label(status: EditOutcomeStatus) -> str:
    return status.value.replace("_", " ").title()


def _usage_text(usage: Any) -> str:
    values = usage.model_dump(mode="json")
    known = {key: value for key, value in values.items() if value is not None}
    return str(known) if known else "unavailable"


def _sanitize_value(value: Any, settings: Settings) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_value(item, settings) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, settings) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item, settings) for item in value)
    if isinstance(value, str):
        clean = value
        for secret in settings.groq_api_key_values:
            clean = clean.replace(secret, "[redacted]")
        return redact_secret_like_text(redact_absolute_path_text(clean))
    return value
