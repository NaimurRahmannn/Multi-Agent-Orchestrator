from __future__ import annotations

from typing import TYPE_CHECKING

from agentorchestra.models import SpecialistName
from agentorchestra.observability_models import (
    AgentTokenUsage,
    ObservedAgentRole,
    RunMetrics,
    RunTimeline,
    TimelineStage,
)

if TYPE_CHECKING:
    from agentorchestra.pipeline_models import EditRunReport


def build_run_metrics(report: EditRunReport, timeline: RunTimeline) -> RunMetrics:
    """Derive metrics only from validated execution evidence."""
    specialist_results = report.specialist_report.results if report.specialist_report else []
    latencies = {name: 0.0 for name in SpecialistName}
    for result in specialist_results:
        latencies[result.specialist] += float(result.latency_ms)

    role_usage: list[AgentTokenUsage] = []
    if report.manager_result is not None:
        role_usage.append(
            AgentTokenUsage(role=ObservedAgentRole.MANAGER, usage=report.manager_result.token_usage)
        )
    role_by_specialist = {
        SpecialistName.HTML: ObservedAgentRole.HTML,
        SpecialistName.CSS: ObservedAgentRole.CSS,
        SpecialistName.SEO: ObservedAgentRole.SEO,
    }
    role_usage.extend(
        AgentTokenUsage(role=role_by_specialist[result.specialist], usage=result.token_usage)
        for result in specialist_results
    )
    if report.qa_run is not None:
        role_usage.append(AgentTokenUsage(role=ObservedAgentRole.QA, usage=report.qa_run.token_usage))

    diff = report.reviewed_diff
    prompt_tokens = _known_sum(role_usage, "prompt_tokens")
    completion_tokens = _known_sum(role_usage, "completion_tokens")
    total_tokens = _known_sum(role_usage, "total_tokens")
    complete = bool(role_usage) and all(
        value is not None for value in (prompt_tokens, completion_tokens, total_tokens)
    )
    promotion_latency = sum(
        event.duration_ms for event in timeline.events if event.stage is TimelineStage.PROMOTION
    )
    return RunMetrics(
        total_latency_ms=float(report.total_latency_ms),
        manager_latency_ms=float(report.manager_result.latency_ms if report.manager_result else 0.0),
        specialist_latency_ms=float(sum(result.latency_ms for result in specialist_results)),
        html_latency_ms=float(latencies[SpecialistName.HTML]),
        css_latency_ms=float(latencies[SpecialistName.CSS]),
        seo_latency_ms=float(latencies[SpecialistName.SEO]),
        lighthouse_latency_ms=float(report.lighthouse_seo.latency_ms if report.lighthouse_seo else 0.0),
        qa_latency_ms=float(report.qa_run.latency_ms if report.qa_run else 0.0),
        screenshot_latency_ms=float(sum(item.latency_ms for item in report.screenshots)),
        promotion_latency_ms=float(promotion_latency),
        applied_patch_count=sum(result.applied_patch_count for result in specialist_results),
        rejected_patch_count=sum(result.rejected_patch_count for result in specialist_results),
        changed_file_count=len(diff.changed_files) if diff else 0,
        added_lines=diff.total_added_lines if diff else 0,
        removed_lines=diff.total_removed_lines if diff else 0,
        lighthouse_score=report.lighthouse_seo.score if report.lighthouse_seo else None,
        token_usage_by_role=role_usage,
        known_prompt_tokens=prompt_tokens,
        known_completion_tokens=completion_tokens,
        known_total_tokens=total_tokens,
        token_usage_complete=complete,
    )


def _known_sum(items: list[AgentTokenUsage], field: str) -> int | None:
    if not items:
        return None
    values = [getattr(item.usage, field) for item in items]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)
