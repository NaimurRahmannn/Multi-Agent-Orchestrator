from __future__ import annotations

from typing import TYPE_CHECKING

from agentorchestra.pipeline_models import EditOutcomeStatus, EditRunReport

from .state import recorder, require_request

if TYPE_CHECKING:
    from .orchestration import AgentOrchestraFlow


def build_report(
    flow: AgentOrchestraFlow,
    *,
    status: EditOutcomeStatus,
    message: str,
    staging_cleaned: bool,
    error: str | None = None,
) -> EditRunReport:
    """Build and persist the public outcome from the current Flow state."""
    promotion = flow.state.promotion_result
    cleanup_warnings = (
        list(promotion.warnings) if promotion is not None else list(flow.state.warnings)
    )
    total_latency_ms = float(
        max(0.0, (flow._dependencies.clock() - flow.state.started_at) * 1000)
    )
    timeline = recorder(flow).snapshot()
    report = EditRunReport(
        request=require_request(flow),
        status=status,
        manager_result=flow.state.manager_result,
        plan=flow.state.plan,
        run_id=flow.state.workspace_run_id,
        specialist_report=flow.state.specialist_report,
        lighthouse_seo=flow.state.lighthouse_seo,
        seo_diagnostic_report=flow.state.seo_diagnostic_report,
        qa_run=flow.state.qa_run,
        reviewed_diff=flow.state.reviewed_diff,
        final_diff=promotion.final_diff if promotion is not None else None,
        promotion_result=promotion,
        promotion_status=promotion.status if promotion is not None else None,
        accepted_content_digest=(
            promotion.accepted_content_digest if promotion is not None else None
        ),
        final_working_digest=(
            promotion.final_working_digest if promotion is not None else None
        ),
        working_updated=promotion.working_updated if promotion is not None else False,
        working_restored=flow.state.working_restored,
        staging_cleaned=staging_cleaned,
        message=message,
        error=error,
        total_latency_ms=total_latency_ms,
        warnings=list(flow.state.warnings),
        cleanup_warnings=cleanup_warnings,
        recovery_required=False,
        screenshots=list(flow.state.screenshots),
        timeline=timeline,
        metrics=None,
    )
    metrics = flow._dependencies.metrics_builder(report, timeline)
    report = EditRunReport.model_validate(
        {**report.model_dump(mode="python"), "metrics": metrics}
    )
    flow.state.timeline = timeline
    flow.state.metrics = metrics
    flow.state.outcome = report
    return report
