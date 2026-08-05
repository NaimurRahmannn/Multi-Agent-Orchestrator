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
from agentorchestra.path_safety import redact_absolute_path_text
from agentorchestra.pipeline_models import (
    EditOutcomeStatus,
    EditRunReport,
    PromotionResult,
    QAEvidenceBundle,
    QARunResult,
    SiteTreeDigest,
)
from agentorchestra.seo_models import (
    LighthouseRunStatus,
    LighthouseSEOResult,
    SEOCompletion,
    SEODiagnosticReport,
    SEOExecutionMode,
)
from agentorchestra.services.lighthouse import run_lighthouse_seo
from agentorchestra.services.promotion import promote_staged_copy
from agentorchestra.services.qa_evidence import (
    build_qa_evidence_bundle,
    validate_execution_evidence,
)
from agentorchestra.services.qa_runner import QARunner
from agentorchestra.services.site_digest import compute_site_tree_digest
from agentorchestra.services.specialist_execution import SpecialistExecutionService
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
    clock: Callable[[], float] = time.perf_counter


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
    working_restored: bool = False
    started_at: float = Field(default=0.0, ge=0)


class AgentOrchestraFlow(Flow[AgentOrchestraFlowState]):
    """Authoritative CrewAI Flow for Manager, specialists, QA, and promotion."""

    _dependencies: AgentOrchestraFlowDependencies = PrivateAttr()
    _workspace_handle: WorkspaceHandle | None = PrivateAttr(default=None)

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
            clock=clock,
        )
        super().__init__(
            initial_state=AgentOrchestraFlowState(),
            tracing=False,
            suppress_flow_events=True,
        )
        self._dependencies = resolved_dependencies
        self._workspace_handle = None

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
        try:
            request = self._require_request()
            manager_result = self._dependencies.manager_router.route(request)
            if manager_result.request != request:
                raise FlowExecutionError("Manager result does not match the requested edit.")
            self.state.manager_result = manager_result
            self.state.plan = manager_result.plan
            return manager_result
        except Exception as exc:
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
            return handle.run_id
        except Exception as exc:
            self._record_failure(exc, "Staged workspace creation failed.")
            return None

    @router(create_workspace, emit=["workspace_ready", "failed"])
    def route_workspace_result(self) -> Literal["workspace_ready", "failed"]:
        return "failed" if self.state.error else "workspace_ready"

    @listen("workspace_ready")
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
            return report
        except Exception as exc:
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
        try:
            plan = self._require_executable_plan()
            if SpecialistName.SEO not in plan.selected_specialists:
                return None
            audit = self._dependencies.lighthouse_runner(
                self._require_workspace(),
                self._require_request().target_page,
                settings=self._dependencies.settings,
            )
            self.state.lighthouse_seo = audit
            if audit.status is not LighthouseRunStatus.SUCCEEDED:
                raise FlowExecutionError(audit.error or "Lighthouse SEO audit failed.")
            return audit
        except Exception as exc:
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
            return evidence
        except Exception as exc:
            self._record_failure(exc, "Execution evidence validation failed.")
            return None

    @router(validate_and_build_qa_evidence, emit=["qa_ready", "failed"])
    def route_evidence_result(self) -> Literal["qa_ready", "failed"]:
        return "failed" if self.state.error else "qa_ready"

    @listen("qa_ready")
    def execute_qa(self) -> QARunResult | None:
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
            return qa_run
        except Exception as exc:
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
            staging_cleaned = self._cleanup_nonaccepted_workspace()
            return self._report(
                status=EditOutcomeStatus.DIAGNOSTIC_COMPLETED,
                message=result.completion.summary,
                staging_cleaned=staging_cleaned,
            )
        except Exception as exc:
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
            return self._report(
                status=EditOutcomeStatus.ACCEPTED,
                message=self._require_qa_run().result.reason,
                staging_cleaned=promotion.staging_cleaned,
            )
        except PromotionRollbackError:
            raise
        except Exception as exc:
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
        self.state.working_restored = False
        self.state.request = request

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
            return False
        return not handle.path.exists() and not handle.path.is_symlink()

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
            total_latency_ms=float(
                max(0.0, (self._dependencies.clock() - self.state.started_at) * 1000)
            ),
            warnings=list(self.state.warnings),
            cleanup_warnings=cleanup_warnings,
            recovery_required=False,
        )
        self.state.outcome = report
        return report

    def _safe_error(self, exc: Exception) -> str:
        clean = str(exc).replace("\n", " ").strip()
        clean = clean.replace(str(self._dependencies.settings.project_root), "[project]")
        for secret in self._dependencies.settings.groq_api_key_values:
            clean = clean.replace(secret, "[redacted]")
        clean = redact_absolute_path_text(clean)
        if isinstance(exc, ExecutionEvidenceError):
            return f"Execution evidence validation failed: {clean[:700]}"
        return clean[:700] or exc.__class__.__name__
