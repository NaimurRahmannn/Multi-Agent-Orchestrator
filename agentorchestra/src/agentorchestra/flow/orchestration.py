from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from crewai.flow import Flow, listen, router, start
from pydantic import PrivateAttr

from agentorchestra.agents.manager import ManagerRouter, ManagerRoutingInterface
from agentorchestra.config import Settings, get_settings
from agentorchestra.models import EditRequest, ManagerRoutingPlan, ManagerRunResult
from agentorchestra.observability_models import RunMetrics, RunTimeline
from agentorchestra.pipeline_models import (
    EditRunReport,
    PromotionResult,
    QAEvidenceBundle,
    QARunResult,
    SiteTreeDigest,
)
from agentorchestra.screenshot_models import ScreenshotArtifact
from agentorchestra.seo_models import LighthouseSEOResult
from agentorchestra.services.computed_styles import verify_computed_style_evidence
from agentorchestra.services.lighthouse import run_lighthouse_seo
from agentorchestra.services.metrics import build_run_metrics
from agentorchestra.services.promotion import promote_staged_copy
from agentorchestra.services.qa_evidence import (
    build_qa_evidence_bundle,
    validate_execution_evidence,
)
from agentorchestra.services.qa_runner import QARunner
from agentorchestra.services.screenshots import capture_page_screenshot
from agentorchestra.services.site_digest import compute_site_tree_digest
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.timeline import RunTimelineRecorder
from agentorchestra.services.workspace import (
    cleanup_staged_workspace,
    create_staged_copy,
    generate_diff,
    get_workspace_handle,
)
from agentorchestra.specialist_models import SpecialistExecutionReport
from agentorchestra.style_models import StyleChangeEvidence
from agentorchestra.workspace_models import DiffReport, WorkspaceHandle

from . import transitions
from .state import AgentOrchestraFlowState


class QAServiceInterface(Protocol):
    def run(self, evidence: QAEvidenceBundle) -> QARunResult:
        """Run QA against deterministic evidence."""


WorkspaceFactory = Callable[..., WorkspaceHandle]
WorkspaceLookup = Callable[..., WorkspaceHandle]
WorkspaceCleanup = Callable[[WorkspaceHandle], None]
DiffGenerator = Callable[..., DiffReport]
EvidenceValidator = Callable[[ManagerRoutingPlan, SpecialistExecutionReport, DiffReport], None]
EvidenceBuilder = Callable[..., QAEvidenceBundle]
PromotionService = Callable[..., PromotionResult]
DigestFunction = Callable[..., SiteTreeDigest]
LighthouseRunner = Callable[..., LighthouseSEOResult]
ScreenshotCapture = Callable[..., ScreenshotArtifact]
TimelineRecorderFactory = Callable[..., RunTimelineRecorder]
MetricsBuilder = Callable[[EditRunReport, RunTimeline], RunMetrics]
ComputedStyleVerifier = Callable[..., list[StyleChangeEvidence]]


@dataclass(frozen=True)
class AgentOrchestraFlowDependencies:
    """Injected lifecycle boundaries used by the real CrewAI transition graph."""

    settings: Settings
    manager_router: ManagerRoutingInterface
    specialist_service: SpecialistExecutionService
    qa_runner: QAServiceInterface
    workspace_factory: WorkspaceFactory = create_staged_copy
    workspace_lookup: WorkspaceLookup = get_workspace_handle
    workspace_cleanup: WorkspaceCleanup = cleanup_staged_workspace
    diff_generator: DiffGenerator = generate_diff
    evidence_validator: EvidenceValidator = validate_execution_evidence
    evidence_builder: EvidenceBuilder = build_qa_evidence_bundle
    promotion_service: PromotionService = promote_staged_copy
    digest_function: DigestFunction = compute_site_tree_digest
    lighthouse_runner: LighthouseRunner = run_lighthouse_seo
    screenshot_capture: ScreenshotCapture | None = None
    timeline_recorder_factory: TimelineRecorderFactory = RunTimelineRecorder
    metrics_builder: MetricsBuilder = build_run_metrics
    computed_style_verifier: ComputedStyleVerifier | None = None
    clock: Callable[[], float] = time.perf_counter


def build_production_flow_dependencies(
    *, settings: Settings | None = None
) -> AgentOrchestraFlowDependencies:
    """Construct live dependencies, including optional screenshot observability."""
    resolved = settings or get_settings()
    return AgentOrchestraFlowDependencies(
        settings=resolved,
        manager_router=ManagerRouter(settings=resolved),
        specialist_service=SpecialistExecutionService(settings=resolved),
        qa_runner=QARunner(settings=resolved),
        screenshot_capture=capture_page_screenshot,
        computed_style_verifier=verify_computed_style_evidence,
    )


class AgentOrchestraFlow(Flow[AgentOrchestraFlowState]):
    """Authoritative CrewAI graph; transition behavior lives in focused modules."""

    _dependencies: AgentOrchestraFlowDependencies = PrivateAttr()
    _workspace_handle: WorkspaceHandle | None = PrivateAttr(default=None)
    _timeline_recorder: RunTimelineRecorder | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        dependencies: AgentOrchestraFlowDependencies | None = None,
        settings: Settings | None = None,
        router: ManagerRoutingInterface | None = None,
        specialist_service: SpecialistExecutionService | None = None,
        qa_runner: QAServiceInterface | None = None,
        workspace_factory: WorkspaceFactory = create_staged_copy,
        workspace_lookup: WorkspaceLookup = get_workspace_handle,
        workspace_cleanup: WorkspaceCleanup = cleanup_staged_workspace,
        diff_generator: DiffGenerator = generate_diff,
        evidence_validator: EvidenceValidator = validate_execution_evidence,
        evidence_builder: EvidenceBuilder = build_qa_evidence_bundle,
        promotion_service: PromotionService = promote_staged_copy,
        digest_function: DigestFunction = compute_site_tree_digest,
        lighthouse_runner: LighthouseRunner = run_lighthouse_seo,
        screenshot_capture: ScreenshotCapture | None = None,
        timeline_recorder_factory: TimelineRecorderFactory = RunTimelineRecorder,
        metrics_builder: MetricsBuilder = build_run_metrics,
        computed_style_verifier: ComputedStyleVerifier | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        resolved_settings = settings or (dependencies.settings if dependencies else get_settings())
        resolved_dependencies = dependencies or AgentOrchestraFlowDependencies(
            settings=resolved_settings,
            manager_router=router or ManagerRouter(settings=resolved_settings),
            specialist_service=specialist_service
            or SpecialistExecutionService(settings=resolved_settings),
            qa_runner=qa_runner or QARunner(settings=resolved_settings),
            workspace_factory=workspace_factory,
            workspace_lookup=workspace_lookup,
            workspace_cleanup=workspace_cleanup,
            diff_generator=diff_generator,
            evidence_validator=evidence_validator,
            evidence_builder=evidence_builder,
            promotion_service=promotion_service,
            digest_function=digest_function,
            lighthouse_runner=lighthouse_runner,
            screenshot_capture=screenshot_capture,
            timeline_recorder_factory=timeline_recorder_factory,
            metrics_builder=metrics_builder,
            computed_style_verifier=computed_style_verifier,
            clock=clock,
        )
        super().__init__(
            initial_state=AgentOrchestraFlowState(),
            tracing=False,
            suppress_flow_events=True,
        )
        self._dependencies = resolved_dependencies
        self._workspace_handle = None
        self._timeline_recorder = None

    def run(self, request: EditRequest | dict[str, str]) -> EditRunReport:
        """Compatibility wrapper that delegates exclusively to CrewAI ``kickoff``."""
        validated = EditRequest.model_validate(request)
        output = self.kickoff(inputs={"request": validated.model_dump(mode="json")})
        return EditRunReport.model_validate(output)

    @start()
    def plan_request(self) -> ManagerRunResult | None:
        return transitions.plan_request(self)

    @router(
        plan_request,
        emit=["clarification", "out_of_scope", "executable", "failed"],
    )
    def route_manager_plan(
        self,
    ) -> Literal["clarification", "out_of_scope", "executable", "failed"]:
        return transitions.route_manager_plan(self)

    @listen("executable")
    def create_workspace(self) -> str | None:
        return transitions.create_workspace(self)

    @router(create_workspace, emit=["workspace_ready", "failed"])
    def route_workspace_result(self) -> Literal["workspace_ready", "failed"]:
        return transitions.route_workspace_result(self)

    @listen("workspace_ready")
    def capture_before_screenshot(self) -> ScreenshotArtifact | None:
        return transitions.capture_before_screenshot(self)

    @router(capture_before_screenshot, emit=["specialists_ready", "failed"])
    def route_before_screenshot(self) -> Literal["specialists_ready", "failed"]:
        return transitions.route_before_screenshot(self)

    @listen("specialists_ready")
    def execute_specialists(self) -> SpecialistExecutionReport | None:
        return transitions.execute_specialists(self)

    @router(
        execute_specialists,
        emit=[
            "specialist_clarification",
            "already_satisfied",
            "blocked",
            "failed",
            "verification_ready",
        ],
    )
    def route_specialist_result(
        self,
    ) -> Literal[
        "specialist_clarification",
        "already_satisfied",
        "blocked",
        "failed",
        "verification_ready",
    ]:
        return transitions.route_specialist_result(self)

    @listen("verification_ready")
    def run_seo_verification(self) -> LighthouseSEOResult | None:
        return transitions.run_seo_verification(self)

    @router(run_seo_verification, emit=["diagnostic_ready", "evidence_ready", "failed"])
    def route_seo_verification(
        self,
    ) -> Literal["diagnostic_ready", "evidence_ready", "failed"]:
        return transitions.route_seo_verification(self)

    @listen("evidence_ready")
    def validate_and_build_qa_evidence(self) -> QAEvidenceBundle | None:
        return transitions.validate_and_build_qa_evidence(self)

    @router(validate_and_build_qa_evidence, emit=["screenshot_ready", "failed"])
    def route_evidence_result(self) -> Literal["screenshot_ready", "failed"]:
        return transitions.route_evidence_result(self)

    @listen("screenshot_ready")
    def capture_proposed_screenshot(self) -> ScreenshotArtifact | None:
        return transitions.capture_proposed_screenshot(self)

    @router(capture_proposed_screenshot, emit=["qa_ready", "failed"])
    def route_proposed_screenshot(self) -> Literal["qa_ready", "failed"]:
        return transitions.route_proposed_screenshot(self)

    @listen("qa_ready")
    def execute_qa(self) -> QARunResult | None:
        return transitions.execute_qa(self)

    @router(execute_qa, emit=["rejected", "accepted", "failed"])
    def route_qa_verdict(self) -> Literal["rejected", "accepted", "failed"]:
        return transitions.route_qa_verdict(self)

    @listen("clarification")
    def finalize_clarification(self) -> EditRunReport:
        return transitions.finalize_clarification(self)

    @listen("out_of_scope")
    def finalize_out_of_scope(self) -> EditRunReport:
        return transitions.finalize_out_of_scope(self)

    @listen("blocked")
    def finalize_blocked(self) -> EditRunReport:
        return transitions.finalize_blocked(self)

    @listen("specialist_clarification")
    def finalize_specialist_clarification(self) -> EditRunReport:
        return transitions.finalize_specialist_clarification(self)

    @listen("already_satisfied")
    def finalize_already_satisfied(self) -> EditRunReport:
        return transitions.finalize_already_satisfied(self)

    @listen("rejected")
    def finalize_rejected(self) -> EditRunReport:
        return transitions.finalize_rejected(self)

    @listen("diagnostic_ready")
    def finalize_seo_diagnostic(self) -> EditRunReport:
        return transitions.finalize_seo_diagnostic(self)

    @listen("accepted")
    def promote_and_finalize(self) -> EditRunReport:
        return transitions.promote_and_finalize(self)

    @listen("failed")
    def finalize_failed(self) -> EditRunReport:
        return transitions.finalize_failed(self)
