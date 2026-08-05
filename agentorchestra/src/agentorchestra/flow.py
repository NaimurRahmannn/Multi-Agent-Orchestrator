from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from crewai.flow import Flow
from pydantic import BaseModel

from agentorchestra.agents.manager import ManagerRouter, ManagerRoutingInterface
from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import (
    AgentOrchestraError,
    ExecutionEvidenceError,
    FlowExecutionError,
)
from agentorchestra.models import (
    EditRequest,
    ManagerRunResult,
    QAVerdict,
    RoutingStatus,
    SpecialistName,
)
from agentorchestra.pipeline_models import (
    EditOutcomeStatus,
    EditRunReport,
    PromotionResult,
    QAEvidenceBundle,
    QARunResult,
)
from agentorchestra.services.promotion import promote_staged_copy
from agentorchestra.services.qa_evidence import (
    build_qa_evidence_bundle,
    validate_execution_evidence,
)
from agentorchestra.services.qa_runner import QARunner
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.workspace import (
    cleanup_staged_workspace,
    create_staged_copy,
    generate_diff,
)
from agentorchestra.specialist_models import SpecialistExecutionReport, SpecialistExecutionStatus
from agentorchestra.workspace_models import DiffReport, WorkspaceHandle


class EditFlowState(BaseModel):
    """Serializable Flow state for the controlled edit lifecycle."""

    request: EditRequest | None = None
    manager_result: ManagerRunResult | None = None
    run_id: str | None = None
    status: str = ""


class QAServiceInterface(Protocol):
    def run(self, evidence: QAEvidenceBundle) -> QARunResult:
        """Run QA against deterministic evidence."""


WorkspaceFactory = Callable[..., WorkspaceHandle]
WorkspaceCleanup = Callable[[WorkspaceHandle], None]
PromotionService = Callable[..., PromotionResult]


class AgentOrchestraFlow(Flow[EditFlowState]):
    """CrewAI Flow boundary that controls staging, QA, promotion, and cleanup."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        router: ManagerRoutingInterface | None = None,
        specialist_service: SpecialistExecutionService | None = None,
        qa_runner: QAServiceInterface | None = None,
        workspace_factory: WorkspaceFactory = create_staged_copy,
        workspace_cleanup: WorkspaceCleanup = cleanup_staged_workspace,
        promotion_service: PromotionService = promote_staged_copy,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        super().__init__()
        self._settings = settings or get_settings()
        self._router = router or ManagerRouter(settings=self._settings)
        self._specialist_service = specialist_service or SpecialistExecutionService(
            settings=self._settings
        )
        self._qa_runner = qa_runner or QARunner(settings=self._settings)
        self._workspace_factory = workspace_factory
        self._workspace_cleanup = workspace_cleanup
        self._promotion_service = promotion_service
        self._clock = clock

    def run(self, request: EditRequest | dict[str, str]) -> EditRunReport:
        """Execute one full apply lifecycle and return a structured report."""
        started = self._clock()
        validated_request = self.plan_request(request)
        self.state.request = validated_request
        handle: WorkspaceHandle | None = None
        manager_result: ManagerRunResult | None = None
        specialist_report: SpecialistExecutionReport | None = None
        reviewed_diff: DiffReport | None = None
        qa_run: QARunResult | None = None
        working_updated = False
        staging_cleaned = True

        try:
            manager_result = self.route_plan(validated_request)
            self.state.manager_result = manager_result
            plan = manager_result.plan
            if plan.status is RoutingStatus.CLARIFICATION_REQUIRED:
                return self._report(
                    request=validated_request,
                    status=EditOutcomeStatus.CLARIFICATION_REQUIRED,
                    manager_result=manager_result,
                    message=plan.clarification_question or "Clarification is required.",
                    started=started,
                    staging_cleaned=True,
                )
            if plan.status is RoutingStatus.OUT_OF_SCOPE:
                return self._report(
                    request=validated_request,
                    status=EditOutcomeStatus.OUT_OF_SCOPE,
                    manager_result=manager_result,
                    message=plan.rejection_reason or "Request is out of scope.",
                    started=started,
                    staging_cleaned=True,
                )
            if SpecialistName.SEO in plan.selected_specialists:
                return self._report(
                    request=validated_request,
                    status=EditOutcomeStatus.UNSUPPORTED_SPECIALIST,
                    manager_result=manager_result,
                    message="SEO execution is not implemented yet.",
                    started=started,
                    staging_cleaned=True,
                )

            handle = self.create_workspace()
            self.state.run_id = handle.run_id
            initial_diff = generate_diff(handle, settings=self._settings)
            if not initial_diff.is_empty:
                raise FlowExecutionError("Fresh staging workspace already differs from working.")

            specialist_report = self.execute_specialists(validated_request, plan, handle)
            reviewed_diff = specialist_report.diff_report
            if specialist_report.status is not SpecialistExecutionStatus.SUCCEEDED:
                status = (
                    EditOutcomeStatus.FAILED
                    if specialist_report.status is SpecialistExecutionStatus.FAILED
                    else EditOutcomeStatus.BLOCKED
                )
                staging_cleaned = self._cleanup(handle)
                return self._report(
                    request=validated_request,
                    status=status,
                    manager_result=manager_result,
                    specialist_report=specialist_report,
                    reviewed_diff=reviewed_diff,
                    message="Specialist execution did not produce a fully successful edit.",
                    error="Specialist execution failed." if status is EditOutcomeStatus.FAILED else None,
                    started=started,
                    staging_cleaned=staging_cleaned,
                )

            evidence = self.validate_evidence(plan, specialist_report, reviewed_diff)
            qa_run = self.execute_qa(evidence)
            if qa_run.result.verdict is QAVerdict.REJECT:
                staging_cleaned = self._cleanup(handle)
                return self._report(
                    request=validated_request,
                    status=EditOutcomeStatus.REJECTED,
                    manager_result=manager_result,
                    specialist_report=specialist_report,
                    qa_run=qa_run,
                    reviewed_diff=reviewed_diff,
                    message=qa_run.result.reason,
                    started=started,
                    staging_cleaned=staging_cleaned,
                )

            promotion = self.finalize_accept_or_reject(handle, reviewed_diff)
            working_updated = promotion.working_updated
            staging_cleaned = self._cleanup(handle)
            return self._report(
                request=validated_request,
                status=EditOutcomeStatus.ACCEPTED,
                manager_result=manager_result,
                specialist_report=specialist_report,
                qa_run=qa_run,
                reviewed_diff=promotion.reviewed_diff,
                final_diff=promotion.final_diff,
                working_updated=working_updated,
                message=qa_run.result.reason,
                started=started,
                staging_cleaned=staging_cleaned,
            )
        except Exception as exc:
            if handle is not None:
                staging_cleaned = self._cleanup(handle)
            return self._report(
                request=validated_request,
                status=EditOutcomeStatus.FAILED,
                manager_result=manager_result,
                specialist_report=specialist_report,
                qa_run=qa_run,
                reviewed_diff=reviewed_diff,
                working_updated=working_updated,
                message="Edit Flow failed safely.",
                error=self._safe_error(exc),
                started=started,
                staging_cleaned=staging_cleaned,
            )

    def plan_request(self, request: EditRequest | dict[str, str]) -> EditRequest:
        return EditRequest.model_validate(request)

    def route_plan(self, request: EditRequest) -> ManagerRunResult:
        return self._router.route(request)

    def create_workspace(self) -> WorkspaceHandle:
        return self._workspace_factory(settings=self._settings)

    def execute_specialists(
        self,
        request: EditRequest,
        plan,
        workspace: WorkspaceHandle,
    ) -> SpecialistExecutionReport:
        return self._specialist_service.execute(request, plan, workspace)

    def validate_evidence(
        self,
        plan,
        specialist_report: SpecialistExecutionReport,
        diff_report: DiffReport,
    ) -> QAEvidenceBundle:
        validate_execution_evidence(plan, specialist_report, diff_report)
        return build_qa_evidence_bundle(
            request=specialist_report.request,
            plan=plan,
            specialist_report=specialist_report,
            diff_report=diff_report,
        )

    def execute_qa(self, evidence: QAEvidenceBundle) -> QARunResult:
        return self._qa_runner.run(evidence)

    def finalize_accept_or_reject(
        self,
        workspace: WorkspaceHandle,
        reviewed_diff: DiffReport,
    ) -> PromotionResult:
        return self._promotion_service(
            workspace,
            reviewed_diff,
            settings=self._settings,
        )

    def _cleanup(self, handle: WorkspaceHandle) -> bool:
        try:
            self._workspace_cleanup(handle)
        except AgentOrchestraError:
            return False
        return not handle.path.exists()

    def _report(
        self,
        *,
        request: EditRequest,
        status: EditOutcomeStatus,
        manager_result: ManagerRunResult | None = None,
        specialist_report: SpecialistExecutionReport | None = None,
        qa_run: QARunResult | None = None,
        reviewed_diff: DiffReport | None = None,
        final_diff: DiffReport | None = None,
        working_updated: bool = False,
        message: str,
        error: str | None = None,
        started: float,
        staging_cleaned: bool,
    ) -> EditRunReport:
        plan = manager_result.plan if manager_result is not None else None
        report = EditRunReport(
            request=request,
            status=status,
            manager_result=manager_result,
            plan=plan,
            run_id=specialist_report.run_id if specialist_report is not None else None,
            specialist_report=specialist_report,
            qa_run=qa_run,
            reviewed_diff=reviewed_diff,
            final_diff=final_diff,
            working_updated=working_updated,
            staging_cleaned=staging_cleaned,
            message=message,
            error=error,
            total_latency_ms=float(max(0.0, (self._clock() - started) * 1000)),
        )
        self.state.status = report.status.value
        return report

    def _safe_error(self, exc: Exception) -> str:
        clean = str(exc).replace("\n", " ").strip()
        for secret in self._settings.groq_api_key_values:
            clean = clean.replace(secret, "[redacted]")
        if isinstance(exc, ExecutionEvidenceError):
            return f"Execution evidence validation failed: {clean[:700]}"
        return clean[:700] or exc.__class__.__name__
