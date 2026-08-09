from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agentorchestra.exceptions import (
    ExecutionEvidenceError,
    FlowExecutionError,
    PromotionError,
    PromotionRollbackError,
)
from agentorchestra.models import ManagerRunResult, QAVerdict, RoutingStatus, SpecialistName
from agentorchestra.observability_models import TimelineEventStatus, TimelineStage
from agentorchestra.pipeline_models import (
    EditOutcomeStatus,
    EditRunReport,
    QAEvidenceBundle,
    QARunResult,
)
from agentorchestra.screenshot_models import (
    ScreenshotArtifact,
    ScreenshotKind,
    ScreenshotStatus,
)
from agentorchestra.seo_models import (
    LighthouseRunStatus,
    LighthouseSEOResult,
    SEOCompletion,
    SEODiagnosticReport,
    SEOExecutionMode,
)
from agentorchestra.specialist_models import (
    SpecialistExecutionReport,
    SpecialistExecutionStatus,
)

from .failure_handler import (
    cleanup_nonaccepted_workspace,
    record_failure,
    record_missing_specialist_failure_events,
    specialist_stage,
)
from .report_builder import build_report
from .state import (
    recorder,
    require_executable_plan,
    require_plan,
    require_qa_run,
    require_request,
    require_reviewed_diff,
    require_specialist_report,
    require_workspace,
    reset_run_state,
)

if TYPE_CHECKING:
    from .orchestration import AgentOrchestraFlow


def plan_request(flow: AgentOrchestraFlow) -> ManagerRunResult | None:
    """Validate the request and perform the first live operation: Manager routing."""
    reset_run_state(flow)
    flow.state.started_at = flow._dependencies.clock()
    token = recorder(flow).start(TimelineStage.MANAGER)
    try:
        request = require_request(flow)
        manager_result = flow._dependencies.manager_router.route(request)
        if manager_result.request != request:
            raise FlowExecutionError("Manager result does not match the requested edit.")
        flow.state.manager_result = manager_result
        flow.state.plan = manager_result.plan
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.SUCCEEDED,
            message=f"Manager returned {manager_result.plan.status.value}.",
            duration_ms=manager_result.latency_ms,
        )
        return manager_result
    except Exception as exc:
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="Manager routing failed safely.",
        )
        record_failure(flow, exc, "Manager routing failed.")
        return None


def route_manager_plan(
    flow: AgentOrchestraFlow,
) -> Literal["clarification", "out_of_scope", "executable", "failed"]:
    if flow.state.error:
        return "failed"
    plan = require_plan(flow)
    if plan.status is RoutingStatus.CLARIFICATION_REQUIRED:
        return "clarification"
    if plan.status is RoutingStatus.OUT_OF_SCOPE:
        return "out_of_scope"
    return "executable"


def create_workspace(flow: AgentOrchestraFlow) -> str | None:
    token = recorder(flow).start(TimelineStage.WORKSPACE)
    try:
        require_executable_plan(flow)
        handle = flow._dependencies.workspace_factory(settings=flow._dependencies.settings)
        flow._workspace_handle = handle
        flow.state.workspace_run_id = handle.run_id
        initial_diff = flow._dependencies.diff_generator(
            handle,
            settings=flow._dependencies.settings,
        )
        if not initial_diff.is_empty:
            raise FlowExecutionError("Fresh staging workspace already differs from working.")
        recorder(flow).set_run_id(handle.run_id)
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.SUCCEEDED,
            message="Fresh staged workspace created and verified empty.",
        )
        return handle.run_id
    except Exception as exc:
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="Staged workspace creation or validation failed.",
        )
        record_failure(flow, exc, "Staged workspace creation failed.")
        return None


def route_workspace_result(flow: AgentOrchestraFlow) -> Literal["workspace_ready", "failed"]:
    return "failed" if flow.state.error else "workspace_ready"


def capture_before_screenshot(flow: AgentOrchestraFlow) -> ScreenshotArtifact | None:
    plan = require_executable_plan(flow)
    if plan.request_type == "seo_diagnostic":
        artifact = _skipped_screenshot(
            flow,
            ScreenshotKind.BEFORE,
            "Screenshots are skipped for SEO diagnostics.",
        )
        flow.state.screenshots.append(artifact)
        _record_screenshot_event(flow, artifact)
        return artifact
    capture = flow._dependencies.screenshot_capture
    if capture is None:
        artifact = _skipped_screenshot(
            flow,
            ScreenshotKind.BEFORE,
            "Screenshot capture is not enabled for this Flow instance.",
        )
        flow.state.screenshots.append(artifact)
        _record_screenshot_event(flow, artifact)
        return artifact
    try:
        working_digest = flow._dependencies.digest_function(
            flow._dependencies.settings.working_site_dir
        )
        artifact = capture(
            settings=flow._dependencies.settings,
            site_root=flow._dependencies.settings.working_site_dir,
            target_page=require_request(flow).target_page,
            run_id=require_workspace(flow).run_id,
            kind=ScreenshotKind.BEFORE,
            source_site_digest=working_digest.digest,
        )
        flow.state.screenshots.append(artifact)
        _record_screenshot_event(flow, artifact)
        _warn_for_screenshot_failure(flow, artifact)
        return artifact
    except Exception as exc:
        record_failure(flow, exc, "Before screenshot safety validation failed.")
        recorder(flow).record(
            TimelineStage.SCREENSHOT_BEFORE,
            status=TimelineEventStatus.FAILED,
            message="Before screenshot failed a safety boundary.",
        )
        return None


def route_before_screenshot(
    flow: AgentOrchestraFlow,
) -> Literal["specialists_ready", "failed"]:
    return "failed" if flow.state.error else "specialists_ready"


def execute_specialists(flow: AgentOrchestraFlow) -> SpecialistExecutionReport | None:
    try:
        report = flow._dependencies.specialist_service.execute(
            require_request(flow),
            require_executable_plan(flow),
            require_workspace(flow),
        )
        if report.run_id != flow.state.workspace_run_id:
            raise FlowExecutionError("Specialist report run ID does not match Flow state.")
        flow.state.specialist_report = report
        flow.state.reviewed_diff = report.diff_report
        for result in report.results:
            status = {
                "succeeded": TimelineEventStatus.SUCCEEDED,
                "blocked": TimelineEventStatus.BLOCKED,
                "failed": TimelineEventStatus.FAILED,
            }[result.status.value]
            recorder(flow).record(
                specialist_stage(result.specialist),
                specialist=result.specialist,
                status=status,
                message=f"{result.specialist.value.upper()} specialist {result.status.value}.",
                duration_ms=result.latency_ms,
            )
        return report
    except Exception as exc:
        record_missing_specialist_failure_events(flow)
        record_failure(flow, exc, "Specialist execution failed.")
        return None


def route_specialist_result(
    flow: AgentOrchestraFlow,
) -> Literal["blocked", "failed", "verification_ready"]:
    if flow.state.error or flow.state.specialist_report is None:
        return "failed"
    status = flow.state.specialist_report.status
    if status is SpecialistExecutionStatus.BLOCKED:
        return "blocked"
    if status is not SpecialistExecutionStatus.SUCCEEDED:
        flow.state.error = "Specialist execution did not produce a fully successful edit."
        flow.state.failure_message = "Specialist execution failed."
        return "failed"
    return "verification_ready"


def run_seo_verification(flow: AgentOrchestraFlow) -> LighthouseSEOResult | None:
    token = None
    try:
        plan = require_executable_plan(flow)
        if SpecialistName.SEO not in plan.selected_specialists:
            return None
        token = recorder(flow).start(TimelineStage.LIGHTHOUSE)
        audit = flow._dependencies.lighthouse_runner(
            require_workspace(flow),
            require_request(flow).target_page,
            settings=flow._dependencies.settings,
        )
        flow.state.lighthouse_seo = audit
        if audit.status is not LighthouseRunStatus.SUCCEEDED:
            raise FlowExecutionError(audit.error or "Lighthouse SEO audit failed.")
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.SUCCEEDED,
            message="Lighthouse SEO audit succeeded.",
            duration_ms=audit.latency_ms,
        )
        return audit
    except Exception as exc:
        if token is not None:
            recorder(flow).finish(
                token,
                status=TimelineEventStatus.FAILED,
                message="Lighthouse SEO audit failed.",
                duration_ms=(
                    flow.state.lighthouse_seo.latency_ms
                    if flow.state.lighthouse_seo is not None
                    else None
                ),
            )
        record_failure(flow, exc, "Lighthouse SEO verification failed.")
        return None


def route_seo_verification(
    flow: AgentOrchestraFlow,
) -> Literal["diagnostic_ready", "evidence_ready", "failed"]:
    if flow.state.error:
        return "failed"
    report = require_specialist_report(flow)
    if report.seo_mode is SEOExecutionMode.DIAGNOSTIC:
        return "diagnostic_ready"
    return "evidence_ready"


def validate_and_build_qa_evidence(
    flow: AgentOrchestraFlow,
) -> QAEvidenceBundle | None:
    token = recorder(flow).start(TimelineStage.EVIDENCE_VALIDATION)
    try:
        plan = require_executable_plan(flow)
        report = require_specialist_report(flow)
        reviewed_diff = require_reviewed_diff(flow)
        flow._dependencies.evidence_validator(plan, report, reviewed_diff)
        staged_digest = flow._dependencies.digest_function(require_workspace(flow).path)
        evidence = flow._dependencies.evidence_builder(
            request=require_request(flow),
            plan=plan,
            specialist_report=report,
            diff_report=reviewed_diff,
            site_content_digest=staged_digest.digest,
            lighthouse_seo=flow.state.lighthouse_seo,
        )
        flow.state.qa_evidence_digest = evidence.evidence_digest
        flow.state.staged_content_digest = staged_digest.digest
        flow.state.qa_evidence = evidence
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.SUCCEEDED,
            message="Deterministic pre-QA evidence validated.",
        )
        return evidence
    except Exception as exc:
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="Deterministic pre-QA evidence validation failed.",
        )
        record_failure(flow, exc, "Execution evidence validation failed.")
        return None


def route_evidence_result(
    flow: AgentOrchestraFlow,
) -> Literal["screenshot_ready", "failed"]:
    return "failed" if flow.state.error else "screenshot_ready"


def capture_proposed_screenshot(flow: AgentOrchestraFlow) -> ScreenshotArtifact | None:
    capture = flow._dependencies.screenshot_capture
    if capture is None:
        artifact = _skipped_screenshot(
            flow,
            ScreenshotKind.PROPOSED_AFTER,
            "Screenshot capture is not enabled for this Flow instance.",
            source_digest=flow.state.staged_content_digest,
        )
        flow.state.screenshots.append(artifact)
        _record_screenshot_event(flow, artifact)
        return artifact
    try:
        artifact = capture(
            settings=flow._dependencies.settings,
            site_root=require_workspace(flow).path,
            target_page=require_request(flow).target_page,
            run_id=require_workspace(flow).run_id,
            kind=ScreenshotKind.PROPOSED_AFTER,
            source_site_digest=flow.state.staged_content_digest,
        )
        flow.state.screenshots.append(artifact)
        _record_screenshot_event(flow, artifact)
        _warn_for_screenshot_failure(flow, artifact)
        return artifact
    except Exception as exc:
        record_failure(flow, exc, "Proposed screenshot safety validation failed.")
        recorder(flow).record(
            TimelineStage.SCREENSHOT_PROPOSED_AFTER,
            status=TimelineEventStatus.FAILED,
            message="Proposed screenshot failed a safety boundary.",
        )
        return None


def route_proposed_screenshot(
    flow: AgentOrchestraFlow,
) -> Literal["qa_ready", "failed"]:
    return "failed" if flow.state.error else "qa_ready"


def execute_qa(flow: AgentOrchestraFlow) -> QARunResult | None:
    token = recorder(flow).start(TimelineStage.QA)
    try:
        evidence = flow.state.qa_evidence
        if evidence is None:
            raise FlowExecutionError("Flow state is missing QA evidence.")
        if evidence.evidence_digest != flow.state.qa_evidence_digest:
            raise FlowExecutionError("QA evidence digest does not match Flow state.")
        qa_run = flow._dependencies.qa_runner.run(evidence)
        if qa_run.evidence_digest != flow.state.qa_evidence_digest:
            raise ExecutionEvidenceError("QA result does not identify the reviewed evidence.")
        if qa_run.site_content_digest != flow.state.staged_content_digest:
            raise ExecutionEvidenceError(
                "QA result does not identify the reviewed site content."
            )
        flow.state.qa_run = qa_run
        recorder(flow).finish(
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
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="QA execution failed.",
        )
        record_failure(flow, exc, "QA execution failed.")
        return None


def route_qa_verdict(
    flow: AgentOrchestraFlow,
) -> Literal["rejected", "accepted", "failed"]:
    if flow.state.error or flow.state.qa_run is None:
        return "failed"
    if flow.state.qa_run.result.verdict is QAVerdict.REJECT:
        return "rejected"
    return "accepted"


def finalize_clarification(flow: AgentOrchestraFlow) -> EditRunReport:
    plan = require_plan(flow)
    return build_report(
        flow,
        status=EditOutcomeStatus.CLARIFICATION_REQUIRED,
        message=plan.clarification_question or "Clarification is required.",
        staging_cleaned=True,
    )


def finalize_out_of_scope(flow: AgentOrchestraFlow) -> EditRunReport:
    plan = require_plan(flow)
    return build_report(
        flow,
        status=EditOutcomeStatus.OUT_OF_SCOPE,
        message=plan.rejection_reason or "Request is out of scope.",
        staging_cleaned=True,
    )


def finalize_blocked(flow: AgentOrchestraFlow) -> EditRunReport:
    staging_cleaned = cleanup_nonaccepted_workspace(flow)
    return build_report(
        flow,
        status=EditOutcomeStatus.BLOCKED,
        message="Specialist execution was blocked before QA.",
        staging_cleaned=staging_cleaned,
    )


def finalize_rejected(flow: AgentOrchestraFlow) -> EditRunReport:
    qa_run = require_qa_run(flow)
    staging_cleaned = cleanup_nonaccepted_workspace(flow)
    return build_report(
        flow,
        status=EditOutcomeStatus.REJECTED,
        message=qa_run.result.reason,
        staging_cleaned=staging_cleaned,
    )


def finalize_seo_diagnostic(flow: AgentOrchestraFlow) -> EditRunReport:
    token = recorder(flow).start(TimelineStage.DIAGNOSTIC_FINALIZE)
    try:
        report = require_specialist_report(flow)
        reviewed_diff = flow.state.reviewed_diff
        if reviewed_diff is None or not reviewed_diff.is_empty:
            raise ExecutionEvidenceError("SEO diagnostic mode must leave staged source unchanged.")
        result = report.results[0]
        if not isinstance(result.completion, SEOCompletion):
            raise ExecutionEvidenceError("SEO diagnostic completion evidence is missing.")
        lighthouse = flow.state.lighthouse_seo
        if lighthouse is None or lighthouse.status is not LighthouseRunStatus.SUCCEEDED:
            raise ExecutionEvidenceError("SEO diagnostic Lighthouse evidence is missing.")
        flow.state.seo_diagnostic_report = SEODiagnosticReport(
            run_id=report.run_id,
            target_page=require_request(flow).target_page,
            findings=result.completion.findings,
            lighthouse=lighthouse,
            source_unchanged=True,
        )
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.SUCCEEDED,
            message="SEO diagnostic evidence finalized without source mutation.",
        )
        staging_cleaned = cleanup_nonaccepted_workspace(flow)
        return build_report(
            flow,
            status=EditOutcomeStatus.DIAGNOSTIC_COMPLETED,
            message=result.completion.summary,
            staging_cleaned=staging_cleaned,
        )
    except Exception as exc:
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="SEO diagnostic finalization failed.",
        )
        record_failure(flow, exc, "SEO diagnostic finalization failed.")
        staging_cleaned = cleanup_nonaccepted_workspace(flow)
        return build_report(
            flow,
            status=EditOutcomeStatus.FAILED,
            message="Edit Flow failed safely.",
            error=flow.state.error,
            staging_cleaned=staging_cleaned,
        )


def promote_and_finalize(flow: AgentOrchestraFlow) -> EditRunReport:
    token = recorder(flow).start(TimelineStage.PROMOTION)
    try:
        handle = require_workspace(flow)
        reviewed_diff = require_reviewed_diff(flow)
        current_diff = flow._dependencies.diff_generator(
            handle,
            settings=flow._dependencies.settings,
        )
        if current_diff != reviewed_diff:
            raise PromotionError("Reviewed staged diff changed before promotion.")
        current_digest = flow._dependencies.digest_function(handle.path)
        current_evidence = flow._dependencies.evidence_builder(
            request=require_request(flow),
            plan=require_executable_plan(flow),
            specialist_report=require_specialist_report(flow),
            diff_report=current_diff,
            site_content_digest=current_digest.digest,
            lighthouse_seo=flow.state.lighthouse_seo,
        )
        if (
            current_evidence.evidence_digest != flow.state.qa_evidence_digest
            or current_digest.digest != flow.state.staged_content_digest
        ):
            raise PromotionError("QA-reviewed evidence changed before promotion.")
        promotion = flow._dependencies.promotion_service(
            handle,
            reviewed_diff,
            settings=flow._dependencies.settings,
        )
        if promotion.accepted_content_digest != flow.state.staged_content_digest:
            raise FlowExecutionError("Promotion digest does not match QA-reviewed content.")
        flow.state.promotion_result = promotion
        flow.state.warnings.extend(promotion.warnings)
        recorder(flow).finish(
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
        return build_report(
            flow,
            status=EditOutcomeStatus.ACCEPTED,
            message=require_qa_run(flow).result.reason,
            staging_cleaned=promotion.staging_cleaned,
        )
    except PromotionRollbackError:
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="Promotion rollback requires operator recovery.",
        )
        raise
    except Exception as exc:
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.FAILED,
            message="Promotion failed safely.",
        )
        record_failure(flow, exc, "Promotion failed.")
        if isinstance(exc, PromotionError):
            flow.state.working_restored = exc.working_restored
        staging_cleaned = cleanup_nonaccepted_workspace(flow)
        return build_report(
            flow,
            status=EditOutcomeStatus.FAILED,
            message="Edit Flow failed safely.",
            error=flow.state.error,
            staging_cleaned=staging_cleaned,
        )


def finalize_failed(flow: AgentOrchestraFlow) -> EditRunReport:
    staging_cleaned = cleanup_nonaccepted_workspace(flow)
    return build_report(
        flow,
        status=EditOutcomeStatus.FAILED,
        message="Edit Flow failed safely.",
        error=flow.state.error or "Flow execution failed.",
        staging_cleaned=staging_cleaned,
    )


def _skipped_screenshot(
    flow: AgentOrchestraFlow,
    kind: ScreenshotKind,
    message: str,
    *,
    source_digest: str | None = None,
) -> ScreenshotArtifact:
    return ScreenshotArtifact(
        kind=kind,
        status=ScreenshotStatus.SKIPPED,
        run_id=require_workspace(flow).run_id,
        target_page=require_request(flow).target_page,
        source_site_digest=source_digest,
        latency_ms=0.0,
        warnings=[message],
    )


def _record_screenshot_event(
    flow: AgentOrchestraFlow,
    artifact: ScreenshotArtifact,
) -> None:
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
    recorder(flow).record(
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


def _warn_for_screenshot_failure(
    flow: AgentOrchestraFlow,
    artifact: ScreenshotArtifact,
) -> None:
    if artifact.status is not ScreenshotStatus.FAILED:
        return
    warning = f"{artifact.kind.value} screenshot unavailable: {artifact.error}"
    if warning not in flow.state.warnings:
        flow.state.warnings.append(warning)
