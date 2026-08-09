from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from agentorchestra.exceptions import FlowExecutionError
from agentorchestra.models import (
    EditRequest,
    ManagerRoutingPlan,
    ManagerRunResult,
    RoutingStatus,
    SpecialistName,
)
from agentorchestra.observability_models import RunMetrics, RunTimeline
from agentorchestra.pipeline_models import (
    EditRunReport,
    PromotionResult,
    QAEvidenceBundle,
    QARunResult,
)
from agentorchestra.screenshot_models import ScreenshotArtifact
from agentorchestra.seo_models import LighthouseSEOResult, SEODiagnosticReport
from agentorchestra.services.timeline import RunTimelineRecorder
from agentorchestra.specialist_models import (
    SpecialistExecutionReport,
    SpecialistExecutionStatus,
)
from agentorchestra.workspace_models import DiffReport, WorkspaceHandle

if TYPE_CHECKING:
    from agentorchestra.flow.orchestration import AgentOrchestraFlow


class AgentOrchestraFlowState(BaseModel):
    """Serializable state carried between CrewAI Flow transitions."""

    request: EditRequest | None = None
    manager_result: ManagerRunResult | None = None
    plan: ManagerRoutingPlan | None = None
    workspace_run_id: str | None = None
    specialist_report: SpecialistExecutionReport | None = None
    reviewed_diff: DiffReport | None = None
    lighthouse_seo: LighthouseSEOResult | None = None
    seo_diagnostic_report: SEODiagnosticReport | None = None
    qa_evidence: QAEvidenceBundle | None = None
    qa_evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    staged_content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    qa_run: QARunResult | None = None
    promotion_result: PromotionResult | None = None
    outcome: EditRunReport | None = None
    error: str | None = None
    failure_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    screenshots: list[ScreenshotArtifact] = Field(default_factory=list)
    timeline: RunTimeline = Field(default_factory=RunTimeline)
    metrics: RunMetrics | None = None
    working_restored: bool = False
    started_at: float = Field(default=0.0, ge=0)


def reset_run_state(flow: AgentOrchestraFlow) -> None:
    """Reset serializable and process-local state before one Flow kickoff."""
    request = flow.state.request
    flow._workspace_handle = None
    flow.state.manager_result = None
    flow.state.plan = None
    flow.state.workspace_run_id = None
    flow.state.specialist_report = None
    flow.state.reviewed_diff = None
    flow.state.lighthouse_seo = None
    flow.state.seo_diagnostic_report = None
    flow.state.qa_evidence = None
    flow.state.qa_evidence_digest = None
    flow.state.staged_content_digest = None
    flow.state.qa_run = None
    flow.state.promotion_result = None
    flow.state.outcome = None
    flow.state.error = None
    flow.state.failure_message = None
    flow.state.warnings = []
    flow.state.screenshots = []
    flow.state.timeline = RunTimeline()
    flow.state.metrics = None
    flow.state.working_restored = False
    flow.state.request = request
    flow._timeline_recorder = flow._dependencies.timeline_recorder_factory(
        run_id=None,
        clock=flow._dependencies.clock,
        settings=flow._dependencies.settings,
    )


def require_request(flow: AgentOrchestraFlow) -> EditRequest:
    if flow.state.request is None:
        raise FlowExecutionError("Flow state is missing the edit request.")
    return EditRequest.model_validate(flow.state.request)


def require_plan(flow: AgentOrchestraFlow) -> ManagerRoutingPlan:
    if flow.state.plan is None or flow.state.manager_result is None:
        raise FlowExecutionError("Flow state is missing the Manager plan.")
    return flow.state.plan


def require_executable_plan(flow: AgentOrchestraFlow) -> ManagerRoutingPlan:
    plan = require_plan(flow)
    if plan.status is not RoutingStatus.EXECUTE:
        raise FlowExecutionError("Flow state does not contain an executable Manager plan.")
    if any(
        specialist not in {SpecialistName.HTML, SpecialistName.CSS, SpecialistName.SEO}
        for specialist in plan.selected_specialists
    ):
        raise FlowExecutionError("Flow state contains an unsupported specialist.")
    return plan


def require_workspace(flow: AgentOrchestraFlow) -> WorkspaceHandle:
    if flow.state.workspace_run_id is None:
        raise FlowExecutionError("Flow state is missing the staged workspace run ID.")
    if flow._workspace_handle is not None:
        if flow._workspace_handle.run_id != flow.state.workspace_run_id:
            raise FlowExecutionError("Runtime workspace does not match Flow state.")
        return flow._workspace_handle
    return flow._dependencies.workspace_lookup(
        flow.state.workspace_run_id,
        settings=flow._dependencies.settings,
    )


def require_specialist_report(flow: AgentOrchestraFlow) -> SpecialistExecutionReport:
    report = flow.state.specialist_report
    if report is None or report.status is not SpecialistExecutionStatus.SUCCEEDED:
        raise FlowExecutionError("Flow state is missing successful specialist evidence.")
    return report


def require_reviewed_diff(flow: AgentOrchestraFlow) -> DiffReport:
    if flow.state.reviewed_diff is None or flow.state.reviewed_diff.is_empty:
        raise FlowExecutionError("Flow state is missing a non-empty reviewed diff.")
    return flow.state.reviewed_diff


def require_qa_run(flow: AgentOrchestraFlow) -> QARunResult:
    if flow.state.qa_run is None:
        raise FlowExecutionError("Flow state is missing the QA result.")
    return flow.state.qa_run


def recorder(flow: AgentOrchestraFlow) -> RunTimelineRecorder:
    if flow._timeline_recorder is None:
        raise FlowExecutionError("Timeline recorder is not initialized.")
    return flow._timeline_recorder
