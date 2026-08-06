from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from crewai.flow import Flow, listen, router, start
from pydantic import BaseModel, Field, PrivateAttr

from agentorchestra.agents.manager import ManagerRouter, ManagerRoutingInterface
from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import (
    ExecutionEvidenceError,
    FlowExecutionError,
    PromotionError,
    PromotionRollbackError,
)
from agentorchestra.models import (
    EditRequest,
    ManagerRoutingPlan,
    ManagerRunResult,
    QAVerdict,
    RoutingStatus,
    SpecialistName,
)
from agentorchestra.observability_models import (
    RunMetrics,
    RunTimeline,
    TimelineEventStatus,
    TimelineStage,
)
from agentorchestra.path_safety import redact_absolute_path_text
from agentorchestra.pipeline_models import (
    EditOutcomeStatus,
    EditRunReport,
    PromotionResult,
    QAEvidenceBundle,
    QARunResult,
    SiteTreeDigest,
)
from agentorchestra.screenshot_models import ScreenshotArtifact, ScreenshotKind, ScreenshotStatus
from agentorchestra.seo_models import (
    LighthouseRunStatus,
    LighthouseSEOResult,
    SEOCompletion,
    SEODiagnosticReport,
    SEOExecutionMode,
)
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
from agentorchestra.specialist_models import SpecialistExecutionReport, SpecialistExecutionStatus
from agentorchestra.workspace_models import DiffReport, WorkspaceHandle


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
    )


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


class AgentOrchestraFlow(Flow[AgentOrchestraFlowState]):
    """Authoritative CrewAI Flow for Manager, specialists, QA, and promotion."""

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
        """Validate the request and perform the first live operation: Manager routing."""
        self._reset_run_state()
        self.state.started_at = self._dependencies.clock()
        token = self._recorder().start(TimelineStage.MANAGER)
        try:
            request = self._require_request()
            manager_result = self._dependencies.manager_router.route(request)
            if manager_result.request != request:
                raise FlowExecutionError("Manager result does not match the requested edit.")
            self.state.manager_result = manager_result
            self.state.plan = manager_result.plan
            self._recorder().finish(
                token,
                status=TimelineEventStatus.SUCCEEDED,
                message=f"Manager returned {manager_result.plan.status.value}.",
                duration_ms=manager_result.latency_ms,
            )
            return manager_result
        except Exception as exc:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="Manager routing failed safely.",
            )
            self._record_failure(exc, "Manager routing failed.")
            return None

    @router(
        plan_request,
        emit=["clarification", "out_of_scope", "executable", "failed"],
    )
    def route_manager_plan(
        self,
    ) -> Literal["clarification", "out_of_scope", "executable", "failed"]:
        if self.state.error:
            return "failed"
        plan = self._require_plan()
        if plan.status is RoutingStatus.CLARIFICATION_REQUIRED:
            return "clarification"
        if plan.status is RoutingStatus.OUT_OF_SCOPE:
            return "out_of_scope"
        return "executable"

    @listen("executable")
    def create_workspace(self) -> str | None:
        token = self._recorder().start(TimelineStage.WORKSPACE)
        try:
            self._require_executable_plan()
            handle = self._dependencies.workspace_factory(settings=self._dependencies.settings)
            self._workspace_handle = handle
            self.state.workspace_run_id = handle.run_id
            initial_diff = self._dependencies.diff_generator(
                handle,
                settings=self._dependencies.settings,
            )
            if not initial_diff.is_empty:
                raise FlowExecutionError("Fresh staging workspace already differs from working.")
            self._recorder().set_run_id(handle.run_id)
            self._recorder().finish(
                token,
                status=TimelineEventStatus.SUCCEEDED,
                message="Fresh staged workspace created and verified empty.",
            )
            return handle.run_id
        except Exception as exc:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="Staged workspace creation or validation failed.",
            )
            self._record_failure(exc, "Staged workspace creation failed.")
            return None

    @router(create_workspace, emit=["workspace_ready", "failed"])
    def route_workspace_result(self) -> Literal["workspace_ready", "failed"]:
        return "failed" if self.state.error else "workspace_ready"

    @listen("workspace_ready")
    def capture_before_screenshot(self) -> ScreenshotArtifact | None:
        plan = self._require_executable_plan()
        if plan.request_type == "seo_diagnostic":
            artifact = self._skipped_screenshot(
                ScreenshotKind.BEFORE,
                "Screenshots are skipped for SEO diagnostics.",
            )
            self.state.screenshots.append(artifact)
            self._record_screenshot_event(artifact)
            return artifact
        capture = self._dependencies.screenshot_capture
        if capture is None:
            artifact = self._skipped_screenshot(
                ScreenshotKind.BEFORE,
                "Screenshot capture is not enabled for this Flow instance.",
            )
            self.state.screenshots.append(artifact)
            self._record_screenshot_event(artifact)
            return artifact
        try:
            working_digest = self._dependencies.digest_function(
                self._dependencies.settings.working_site_dir
            )
            artifact = capture(
                settings=self._dependencies.settings,
                site_root=self._dependencies.settings.working_site_dir,
                target_page=self._require_request().target_page,
                run_id=self._require_workspace().run_id,
                kind=ScreenshotKind.BEFORE,
                source_site_digest=working_digest.digest,
            )
            self.state.screenshots.append(artifact)
            self._record_screenshot_event(artifact)
            self._warn_for_screenshot_failure(artifact)
            return artifact
        except Exception as exc:
            self._record_failure(exc, "Before screenshot safety validation failed.")
            self._recorder().record(
                TimelineStage.SCREENSHOT_BEFORE,
                status=TimelineEventStatus.FAILED,
                message="Before screenshot failed a safety boundary.",
            )
            return None

    @router(capture_before_screenshot, emit=["specialists_ready", "failed"])
    def route_before_screenshot(self) -> Literal["specialists_ready", "failed"]:
        return "failed" if self.state.error else "specialists_ready"

    @listen("specialists_ready")
    def execute_specialists(self) -> SpecialistExecutionReport | None:
        try:
            report = self._dependencies.specialist_service.execute(
                self._require_request(),
                self._require_executable_plan(),
                self._require_workspace(),
            )
            if report.run_id != self.state.workspace_run_id:
                raise FlowExecutionError("Specialist report run ID does not match Flow state.")
            self.state.specialist_report = report
            self.state.reviewed_diff = report.diff_report
            for result in report.results:
                status = {
                    "succeeded": TimelineEventStatus.SUCCEEDED,
                    "blocked": TimelineEventStatus.BLOCKED,
                    "failed": TimelineEventStatus.FAILED,
                }[result.status.value]
                self._recorder().record(
                    self._specialist_stage(result.specialist),
                    specialist=result.specialist,
                    status=status,
                    message=f"{result.specialist.value.upper()} specialist {result.status.value}.",
                    duration_ms=result.latency_ms,
                )
            return report
        except Exception as exc:
            self._record_missing_specialist_failure_events()
            self._record_failure(exc, "Specialist execution failed.")
            return None

    @router(execute_specialists, emit=["blocked", "failed", "verification_ready"])
    def route_specialist_result(self) -> Literal["blocked", "failed", "verification_ready"]:
        if self.state.error or self.state.specialist_report is None:
            return "failed"
        status = self.state.specialist_report.status
        if status is SpecialistExecutionStatus.BLOCKED:
            return "blocked"
        if status is not SpecialistExecutionStatus.SUCCEEDED:
            self.state.error = "Specialist execution did not produce a fully successful edit."
            self.state.failure_message = "Specialist execution failed."
            return "failed"
        return "verification_ready"

    @listen("verification_ready")
    def run_seo_verification(self) -> LighthouseSEOResult | None:
        token = None
        try:
            plan = self._require_executable_plan()
            if SpecialistName.SEO not in plan.selected_specialists:
                return None
            token = self._recorder().start(TimelineStage.LIGHTHOUSE)
            audit = self._dependencies.lighthouse_runner(
                self._require_workspace(),
                self._require_request().target_page,
                settings=self._dependencies.settings,
            )
            self.state.lighthouse_seo = audit
            if audit.status is not LighthouseRunStatus.SUCCEEDED:
                raise FlowExecutionError(audit.error or "Lighthouse SEO audit failed.")
            self._recorder().finish(
                token,
                status=TimelineEventStatus.SUCCEEDED,
                message="Lighthouse SEO audit succeeded.",
                duration_ms=audit.latency_ms,
            )
            return audit
        except Exception as exc:
            if token is not None:
                self._recorder().finish(
                    token,
                    status=TimelineEventStatus.FAILED,
                    message="Lighthouse SEO audit failed.",
                    duration_ms=(
                        self.state.lighthouse_seo.latency_ms
                        if self.state.lighthouse_seo is not None
                        else None
                    ),
                )
            self._record_failure(exc, "Lighthouse SEO verification failed.")
            return None

    @router(run_seo_verification, emit=["diagnostic_ready", "evidence_ready", "failed"])
    def route_seo_verification(
        self,
    ) -> Literal["diagnostic_ready", "evidence_ready", "failed"]:
        if self.state.error:
            return "failed"
        report = self._require_specialist_report()
        if report.seo_mode is SEOExecutionMode.DIAGNOSTIC:
            return "diagnostic_ready"
        return "evidence_ready"

    @listen("evidence_ready")
    def validate_and_build_qa_evidence(self) -> QAEvidenceBundle | None:
        token = self._recorder().start(TimelineStage.EVIDENCE_VALIDATION)
        try:
            plan = self._require_executable_plan()
            report = self._require_specialist_report()
            reviewed_diff = self._require_reviewed_diff()
            self._dependencies.evidence_validator(plan, report, reviewed_diff)
            staged_digest = self._dependencies.digest_function(self._require_workspace().path)
            evidence = self._dependencies.evidence_builder(
                request=self._require_request(),
                plan=plan,
                specialist_report=report,
                diff_report=reviewed_diff,
                site_content_digest=staged_digest.digest,
                lighthouse_seo=self.state.lighthouse_seo,
            )
            self.state.qa_evidence_digest = evidence.evidence_digest
            self.state.staged_content_digest = staged_digest.digest
            self.state.qa_evidence = evidence
            self._recorder().finish(
                token,
                status=TimelineEventStatus.SUCCEEDED,
                message="Deterministic pre-QA evidence validated.",
            )
            return evidence
        except Exception as exc:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="Deterministic pre-QA evidence validation failed.",
            )
            self._record_failure(exc, "Execution evidence validation failed.")
            return None

    @router(validate_and_build_qa_evidence, emit=["screenshot_ready", "failed"])
    def route_evidence_result(self) -> Literal["screenshot_ready", "failed"]:
        return "failed" if self.state.error else "screenshot_ready"

    @listen("screenshot_ready")
    def capture_proposed_screenshot(self) -> ScreenshotArtifact | None:
        capture = self._dependencies.screenshot_capture
        if capture is None:
            artifact = self._skipped_screenshot(
                ScreenshotKind.PROPOSED_AFTER,
                "Screenshot capture is not enabled for this Flow instance.",
                source_digest=self.state.staged_content_digest,
            )
            self.state.screenshots.append(artifact)
            self._record_screenshot_event(artifact)
            return artifact
        try:
            artifact = capture(
                settings=self._dependencies.settings,
                site_root=self._require_workspace().path,
                target_page=self._require_request().target_page,
                run_id=self._require_workspace().run_id,
                kind=ScreenshotKind.PROPOSED_AFTER,
                source_site_digest=self.state.staged_content_digest,
            )
            self.state.screenshots.append(artifact)
            self._record_screenshot_event(artifact)
            self._warn_for_screenshot_failure(artifact)
            return artifact
        except Exception as exc:
            self._record_failure(exc, "Proposed screenshot safety validation failed.")
            self._recorder().record(
                TimelineStage.SCREENSHOT_PROPOSED_AFTER,
                status=TimelineEventStatus.FAILED,
                message="Proposed screenshot failed a safety boundary.",
            )
            return None

    @router(capture_proposed_screenshot, emit=["qa_ready", "failed"])
    def route_proposed_screenshot(self) -> Literal["qa_ready", "failed"]:
        return "failed" if self.state.error else "qa_ready"

    @listen("qa_ready")
    def execute_qa(self) -> QARunResult | None:
        token = self._recorder().start(TimelineStage.QA)
        try:
            evidence = self.state.qa_evidence
            if evidence is None:
                raise FlowExecutionError("Flow state is missing QA evidence.")
            if evidence.evidence_digest != self.state.qa_evidence_digest:
                raise FlowExecutionError("QA evidence digest does not match Flow state.")
            qa_run = self._dependencies.qa_runner.run(evidence)
            if qa_run.evidence_digest != self.state.qa_evidence_digest:
                raise ExecutionEvidenceError("QA result does not identify the reviewed evidence.")
            if qa_run.site_content_digest != self.state.staged_content_digest:
                raise ExecutionEvidenceError(
                    "QA result does not identify the reviewed site content."
                )
            self.state.qa_run = qa_run
            self._recorder().finish(
                token,
                status=(
                    TimelineEventStatus.SUCCEEDED
                    if qa_run.result.verdict is QAVerdict.ACCEPT
                    else TimelineEventStatus.REJECTED
                ),
                message=f"QA returned {qa_run.result.verdict.value}.",
                duration_ms=qa_run.latency_ms,
            )
            return qa_run
        except Exception as exc:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="QA execution failed.",
            )
            self._record_failure(exc, "QA execution failed.")
            return None

    @router(execute_qa, emit=["rejected", "accepted", "failed"])
    def route_qa_verdict(self) -> Literal["rejected", "accepted", "failed"]:
        if self.state.error or self.state.qa_run is None:
            return "failed"
        if self.state.qa_run.result.verdict is QAVerdict.REJECT:
            return "rejected"
        return "accepted"

    @listen("clarification")
    def finalize_clarification(self) -> EditRunReport:
        plan = self._require_plan()
        return self._report(
            status=EditOutcomeStatus.CLARIFICATION_REQUIRED,
            message=plan.clarification_question or "Clarification is required.",
            staging_cleaned=True,
        )

    @listen("out_of_scope")
    def finalize_out_of_scope(self) -> EditRunReport:
        plan = self._require_plan()
        return self._report(
            status=EditOutcomeStatus.OUT_OF_SCOPE,
            message=plan.rejection_reason or "Request is out of scope.",
            staging_cleaned=True,
        )

    @listen("blocked")
    def finalize_blocked(self) -> EditRunReport:
        staging_cleaned = self._cleanup_nonaccepted_workspace()
        return self._report(
            status=EditOutcomeStatus.BLOCKED,
            message="Specialist execution was blocked before QA.",
            staging_cleaned=staging_cleaned,
        )

    @listen("rejected")
    def finalize_rejected(self) -> EditRunReport:
        qa_run = self._require_qa_run()
        staging_cleaned = self._cleanup_nonaccepted_workspace()
        return self._report(
            status=EditOutcomeStatus.REJECTED,
            message=qa_run.result.reason,
            staging_cleaned=staging_cleaned,
        )

    @listen("diagnostic_ready")
    def finalize_seo_diagnostic(self) -> EditRunReport:
        token = self._recorder().start(TimelineStage.DIAGNOSTIC_FINALIZE)
        try:
            report = self._require_specialist_report()
            reviewed_diff = self.state.reviewed_diff
            if reviewed_diff is None or not reviewed_diff.is_empty:
                raise ExecutionEvidenceError(
                    "SEO diagnostic mode must leave staged source unchanged."
                )
            result = report.results[0]
            if not isinstance(result.completion, SEOCompletion):
                raise ExecutionEvidenceError("SEO diagnostic completion evidence is missing.")
            lighthouse = self.state.lighthouse_seo
            if lighthouse is None or lighthouse.status is not LighthouseRunStatus.SUCCEEDED:
                raise ExecutionEvidenceError("SEO diagnostic Lighthouse evidence is missing.")
            self.state.seo_diagnostic_report = SEODiagnosticReport(
                run_id=report.run_id,
                target_page=self._require_request().target_page,
                findings=result.completion.findings,
                lighthouse=lighthouse,
                source_unchanged=True,
            )
            self._recorder().finish(
                token,
                status=TimelineEventStatus.SUCCEEDED,
                message="SEO diagnostic evidence finalized without source mutation.",
            )
            staging_cleaned = self._cleanup_nonaccepted_workspace()
            return self._report(
                status=EditOutcomeStatus.DIAGNOSTIC_COMPLETED,
                message=result.completion.summary,
                staging_cleaned=staging_cleaned,
            )
        except Exception as exc:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="SEO diagnostic finalization failed.",
            )
            self._record_failure(exc, "SEO diagnostic finalization failed.")
            staging_cleaned = self._cleanup_nonaccepted_workspace()
            return self._report(
                status=EditOutcomeStatus.FAILED,
                message="Edit Flow failed safely.",
                error=self.state.error,
                staging_cleaned=staging_cleaned,
            )

    @listen("accepted")
    def promote_and_finalize(self) -> EditRunReport:
        token = self._recorder().start(TimelineStage.PROMOTION)
        try:
            handle = self._require_workspace()
            reviewed_diff = self._require_reviewed_diff()
            current_diff = self._dependencies.diff_generator(
                handle,
                settings=self._dependencies.settings,
            )
            if current_diff != reviewed_diff:
                raise PromotionError("Reviewed staged diff changed before promotion.")
            current_digest = self._dependencies.digest_function(handle.path)
            current_evidence = self._dependencies.evidence_builder(
                request=self._require_request(),
                plan=self._require_executable_plan(),
                specialist_report=self._require_specialist_report(),
                diff_report=current_diff,
                site_content_digest=current_digest.digest,
                lighthouse_seo=self.state.lighthouse_seo,
            )
            if (
                current_evidence.evidence_digest != self.state.qa_evidence_digest
                or current_digest.digest != self.state.staged_content_digest
            ):
                raise PromotionError("QA-reviewed evidence changed before promotion.")
            promotion = self._dependencies.promotion_service(
                handle,
                reviewed_diff,
                settings=self._dependencies.settings,
            )
            if promotion.accepted_content_digest != self.state.staged_content_digest:
                raise FlowExecutionError("Promotion digest does not match QA-reviewed content.")
            self.state.promotion_result = promotion
            self.state.warnings.extend(promotion.warnings)
            self._recorder().finish(
                token,
                status=(
                    TimelineEventStatus.WARNING
                    if promotion.warnings
                    else TimelineEventStatus.SUCCEEDED
                ),
                message=(
                    "Working site promoted with a cleanup warning."
                    if promotion.warnings
                    else "QA-reviewed staged content promoted transactionally."
                ),
            )
            return self._report(
                status=EditOutcomeStatus.ACCEPTED,
                message=self._require_qa_run().result.reason,
                staging_cleaned=promotion.staging_cleaned,
            )
        except PromotionRollbackError:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="Promotion rollback requires operator recovery.",
            )
            raise
        except Exception as exc:
            self._recorder().finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="Promotion failed safely.",
            )
            self._record_failure(exc, "Promotion failed.")
            if isinstance(exc, PromotionError):
                self.state.working_restored = exc.working_restored
            staging_cleaned = self._cleanup_nonaccepted_workspace()
            return self._report(
                status=EditOutcomeStatus.FAILED,
                message="Edit Flow failed safely.",
                error=self.state.error,
                staging_cleaned=staging_cleaned,
            )

    @listen("failed")
    def finalize_failed(self) -> EditRunReport:
        staging_cleaned = self._cleanup_nonaccepted_workspace()
        return self._report(
            status=EditOutcomeStatus.FAILED,
            message="Edit Flow failed safely.",
            error=self.state.error or "Flow execution failed.",
            staging_cleaned=staging_cleaned,
        )

    def _reset_run_state(self) -> None:
        request = self.state.request
        self._workspace_handle = None
        self.state.manager_result = None
        self.state.plan = None
        self.state.workspace_run_id = None
        self.state.specialist_report = None
        self.state.reviewed_diff = None
        self.state.lighthouse_seo = None
        self.state.seo_diagnostic_report = None
        self.state.qa_evidence = None
        self.state.qa_evidence_digest = None
        self.state.staged_content_digest = None
        self.state.qa_run = None
        self.state.promotion_result = None
        self.state.outcome = None
        self.state.error = None
        self.state.failure_message = None
        self.state.warnings = []
        self.state.screenshots = []
        self.state.timeline = RunTimeline()
        self.state.metrics = None
        self.state.working_restored = False
        self.state.request = request
        self._timeline_recorder = self._dependencies.timeline_recorder_factory(
            run_id=None,
            clock=self._dependencies.clock,
            settings=self._dependencies.settings,
        )

    def _require_request(self) -> EditRequest:
        if self.state.request is None:
            raise FlowExecutionError("Flow state is missing the edit request.")
        return EditRequest.model_validate(self.state.request)

    def _require_plan(self) -> ManagerRoutingPlan:
        if self.state.plan is None or self.state.manager_result is None:
            raise FlowExecutionError("Flow state is missing the Manager plan.")
        return self.state.plan

    def _require_executable_plan(self) -> ManagerRoutingPlan:
        plan = self._require_plan()
        if plan.status is not RoutingStatus.EXECUTE:
            raise FlowExecutionError("Flow state does not contain an executable Manager plan.")
        if any(
            specialist not in {SpecialistName.HTML, SpecialistName.CSS, SpecialistName.SEO}
            for specialist in plan.selected_specialists
        ):
            raise FlowExecutionError("Flow state contains an unsupported specialist.")
        return plan

    def _require_workspace(self) -> WorkspaceHandle:
        if self.state.workspace_run_id is None:
            raise FlowExecutionError("Flow state is missing the staged workspace run ID.")
        if self._workspace_handle is not None:
            if self._workspace_handle.run_id != self.state.workspace_run_id:
                raise FlowExecutionError("Runtime workspace does not match Flow state.")
            return self._workspace_handle
        return self._dependencies.workspace_lookup(
            self.state.workspace_run_id,
            settings=self._dependencies.settings,
        )

    def _require_specialist_report(self) -> SpecialistExecutionReport:
        report = self.state.specialist_report
        if report is None or report.status is not SpecialistExecutionStatus.SUCCEEDED:
            raise FlowExecutionError("Flow state is missing successful specialist evidence.")
        return report

    def _require_reviewed_diff(self) -> DiffReport:
        if self.state.reviewed_diff is None or self.state.reviewed_diff.is_empty:
            raise FlowExecutionError("Flow state is missing a non-empty reviewed diff.")
        return self.state.reviewed_diff

    def _require_qa_run(self) -> QARunResult:
        if self.state.qa_run is None:
            raise FlowExecutionError("Flow state is missing the QA result.")
        return self.state.qa_run

    def _cleanup_nonaccepted_workspace(self) -> bool:
        if self.state.workspace_run_id is None:
            return True
        token = self._recorder().start(TimelineStage.CLEANUP)
        try:
            handle = self._workspace_handle or self._dependencies.workspace_lookup(
                self.state.workspace_run_id,
                settings=self._dependencies.settings,
            )
            self._dependencies.workspace_cleanup(handle)
        except Exception:
            warning = f"Could not remove staged run '{self.state.workspace_run_id}'."
            if warning not in self.state.warnings:
                self.state.warnings.append(warning)
            self._recorder().finish(
                token,
                status=TimelineEventStatus.WARNING,
                message="Staged workspace cleanup could not be verified.",
            )
            return False
        cleaned = not handle.path.exists() and not handle.path.is_symlink()
        self._recorder().finish(
            token,
            status=(
                TimelineEventStatus.SUCCEEDED if cleaned else TimelineEventStatus.WARNING
            ),
            message=(
                "Discarded staged workspace cleaned."
                if cleaned
                else "Staged workspace cleanup could not be verified."
            ),
        )
        return cleaned

    def _record_failure(self, exc: Exception, message: str) -> None:
        if isinstance(exc, PromotionRollbackError):
            raise exc
        self.state.error = self._safe_error(exc)
        self.state.failure_message = message

    def _report(
        self,
        *,
        status: EditOutcomeStatus,
        message: str,
        staging_cleaned: bool,
        error: str | None = None,
    ) -> EditRunReport:
        promotion = self.state.promotion_result
        cleanup_warnings = (
            list(promotion.warnings) if promotion is not None else list(self.state.warnings)
        )
        total_latency_ms = float(
            max(0.0, (self._dependencies.clock() - self.state.started_at) * 1000)
        )
        timeline = self._recorder().snapshot()
        report = EditRunReport(
            request=self._require_request(),
            status=status,
            manager_result=self.state.manager_result,
            plan=self.state.plan,
            run_id=self.state.workspace_run_id,
            specialist_report=self.state.specialist_report,
            lighthouse_seo=self.state.lighthouse_seo,
            seo_diagnostic_report=self.state.seo_diagnostic_report,
            qa_run=self.state.qa_run,
            reviewed_diff=self.state.reviewed_diff,
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
            working_restored=self.state.working_restored,
            staging_cleaned=staging_cleaned,
            message=message,
            error=error,
            total_latency_ms=total_latency_ms,
            warnings=list(self.state.warnings),
            cleanup_warnings=cleanup_warnings,
            recovery_required=False,
            screenshots=list(self.state.screenshots),
            timeline=timeline,
            metrics=None,
        )
        metrics = self._dependencies.metrics_builder(report, timeline)
        report = EditRunReport.model_validate(
            {**report.model_dump(mode="python"), "metrics": metrics}
        )
        self.state.timeline = timeline
        self.state.metrics = metrics
        self.state.outcome = report
        return report

    def _recorder(self) -> RunTimelineRecorder:
        if self._timeline_recorder is None:
            raise FlowExecutionError("Timeline recorder is not initialized.")
        return self._timeline_recorder

    def _skipped_screenshot(
        self,
        kind: ScreenshotKind,
        message: str,
        *,
        source_digest: str | None = None,
    ) -> ScreenshotArtifact:
        return ScreenshotArtifact(
            kind=kind,
            status=ScreenshotStatus.SKIPPED,
            run_id=self._require_workspace().run_id,
            target_page=self._require_request().target_page,
            source_site_digest=source_digest,
            latency_ms=0.0,
            warnings=[message],
        )

    def _record_screenshot_event(self, artifact: ScreenshotArtifact) -> None:
        stage = (
            TimelineStage.SCREENSHOT_BEFORE
            if artifact.kind is ScreenshotKind.BEFORE
            else TimelineStage.SCREENSHOT_PROPOSED_AFTER
        )
        status = {
            ScreenshotStatus.SUCCEEDED: TimelineEventStatus.SUCCEEDED,
            ScreenshotStatus.FAILED: TimelineEventStatus.WARNING,
            ScreenshotStatus.SKIPPED: TimelineEventStatus.SKIPPED,
        }[artifact.status]
        self._recorder().record(
            stage,
            status=status,
            message=(
                "Screenshot captured and verified."
                if artifact.status is ScreenshotStatus.SUCCEEDED
                else "Screenshot unavailable; core lifecycle continued."
                if artifact.status is ScreenshotStatus.FAILED
                else artifact.warnings[0]
            ),
            duration_ms=artifact.latency_ms,
        )

    def _warn_for_screenshot_failure(self, artifact: ScreenshotArtifact) -> None:
        if artifact.status is not ScreenshotStatus.FAILED:
            return
        warning = f"{artifact.kind.value} screenshot unavailable: {artifact.error}"
        if warning not in self.state.warnings:
            self.state.warnings.append(warning)

    @staticmethod
    def _specialist_stage(specialist: SpecialistName) -> TimelineStage:
        return {
            SpecialistName.HTML: TimelineStage.SPECIALIST_HTML,
            SpecialistName.CSS: TimelineStage.SPECIALIST_CSS,
            SpecialistName.SEO: TimelineStage.SPECIALIST_SEO,
        }[specialist]

    def _record_missing_specialist_failure_events(self) -> None:
        plan = self.state.plan
        if plan is None:
            return
        recorded = {
            event.specialist
            for event in self._recorder().snapshot().events
            if event.specialist is not None
        }
        for specialist in plan.selected_specialists:
            if specialist not in recorded:
                self._recorder().record(
                    self._specialist_stage(specialist),
                    specialist=specialist,
                    status=TimelineEventStatus.FAILED,
                    message=f"{specialist.value.upper()} specialist failed safely.",
                )

    def _safe_error(self, exc: Exception) -> str:
        clean = str(exc).replace("\n", " ").strip()
        clean = clean.replace(str(self._dependencies.settings.project_root), "[project]")
        for secret in self._dependencies.settings.groq_api_key_values:
            clean = clean.replace(secret, "[redacted]")
        clean = redact_absolute_path_text(clean)
        if isinstance(exc, ExecutionEvidenceError):
            return f"Execution evidence validation failed: {clean[:700]}"
        return clean[:700] or exc.__class__.__name__
