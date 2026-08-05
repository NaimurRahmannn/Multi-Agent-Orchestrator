from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from agentorchestra.config import Settings
from agentorchestra.exceptions import (
    SpecialistExecutionError,
    SpecialistPlanError,
    UnsupportedSpecialistError,
)
from agentorchestra.models import (
    SEO_DIAGNOSTIC_REQUEST_TYPE,
    EditRequest,
    ManagerRoutingPlan,
    RoutingStatus,
    SpecialistAssignment,
    SpecialistName,
)
from agentorchestra.seo_models import SEOExecutionMode
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import generate_diff, read_file, validate_staged_site
from agentorchestra.specialist_models import (
    SpecialistExecutionReport,
    SpecialistExecutionStatus,
    SpecialistRunResult,
    SpecialistRunStatus,
)
from agentorchestra.workspace_models import WorkspaceHandle


class SpecialistRunnerInterface(Protocol):
    def run_specialist(
        self,
        request: EditRequest,
        assignment: SpecialistAssignment,
        acceptance_criteria: list[str],
        workspace: WorkspaceHandle,
        mode: SEOExecutionMode = SEOExecutionMode.EDIT,
    ) -> SpecialistRunResult:
        """Execute one selected specialist."""


class SpecialistExecutionService:
    """Sequentially execute only a validated HTML/CSS Manager plan in existing staging."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        runner: SpecialistRunnerInterface | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner or SpecialistRunner(settings=settings)

    def execute(
        self,
        request: EditRequest,
        plan: ManagerRoutingPlan,
        workspace: WorkspaceHandle,
    ) -> SpecialistExecutionReport:
        validated_request, validated_plan = self._validate_inputs(request, plan, workspace)
        results: list[SpecialistRunResult] = []
        seo_mode = None
        if SpecialistName.SEO in validated_plan.selected_specialists:
            seo_mode = (
                SEOExecutionMode.DIAGNOSTIC
                if validated_plan.request_type == SEO_DIAGNOSTIC_REQUEST_TYPE
                else SEOExecutionMode.EDIT
            )

        for assignment in validated_plan.assignments:
            if assignment.agent is SpecialistName.SEO:
                result = self._runner.run_specialist(
                    validated_request,
                    assignment,
                    validated_plan.acceptance_criteria,
                    workspace,
                    mode=seo_mode or SEOExecutionMode.EDIT,
                )
            else:
                result = self._runner.run_specialist(
                    validated_request,
                    assignment,
                    validated_plan.acceptance_criteria,
                    workspace,
                )
            if result.specialist is not assignment.agent or result.assignment != assignment.task:
                raise SpecialistExecutionError(
                    "Specialist runner returned evidence for a different assignment."
                )
            results.append(result)
            if result.status in {SpecialistRunStatus.BLOCKED, SpecialistRunStatus.FAILED}:
                break

        try:
            validate_staged_site(workspace)
            diff_report = generate_diff(workspace, settings=self._settings)
        except Exception as exc:
            raise SpecialistExecutionError("Final staged diff generation failed safely.") from exc

        if results and seo_mode is SEOExecutionMode.DIAGNOSTIC and not diff_report.is_empty:
            raise SpecialistExecutionError("SEO diagnostic execution modified staged source.")
        elif (
            results
            and seo_mode is not SEOExecutionMode.DIAGNOSTIC
            and all(result.status is SpecialistRunStatus.SUCCEEDED for result in results)
            and diff_report.is_empty
        ):
            raise SpecialistExecutionError("Successful edit execution produced no staged diff.")

        stopped_early = len(results) < len(validated_plan.selected_specialists)
        return SpecialistExecutionReport(
            run_id=workspace.run_id,
            request=validated_request,
            plan=validated_plan,
            status=_overall_status(results, all_selected=not stopped_early),
            results=results,
            diff_report=diff_report,
            total_latency_ms=float(sum(result.latency_ms for result in results)),
            stopped_early=stopped_early,
            seo_mode=seo_mode,
        )

    def _validate_inputs(
        self,
        request: EditRequest,
        plan: ManagerRoutingPlan,
        workspace: WorkspaceHandle,
    ) -> tuple[EditRequest, ManagerRoutingPlan]:
        try:
            validated_request = EditRequest.model_validate(request)
            validated_plan = ManagerRoutingPlan.model_validate(plan)
        except ValidationError as exc:
            raise SpecialistPlanError("Specialist execution inputs are invalid.") from exc
        if validated_plan.status is not RoutingStatus.EXECUTE:
            raise SpecialistPlanError(
                f"Manager plan status {validated_plan.status.value} is not executable."
            )
        if any(
            specialist not in {SpecialistName.HTML, SpecialistName.CSS, SpecialistName.SEO}
            for specialist in validated_plan.selected_specialists
        ):
            raise UnsupportedSpecialistError("Manager plan contains an unsupported specialist.")
        try:
            validate_staged_site(workspace)
            read_file(
                workspace,
                file=validated_request.target_page,
                start_line=1,
                end_line=1,
                allowed_files=(validated_request.target_page,),
            )
        except Exception as exc:
            raise SpecialistPlanError("Workspace or selected target page is invalid.") from exc
        return validated_request, validated_plan


def _overall_status(
    results: list[SpecialistRunResult], *, all_selected: bool
) -> SpecialistExecutionStatus:
    succeeded = sum(result.status is SpecialistRunStatus.SUCCEEDED for result in results)
    if succeeded == len(results) and succeeded and all_selected:
        return SpecialistExecutionStatus.SUCCEEDED
    if succeeded:
        return SpecialistExecutionStatus.PARTIAL
    if any(result.status is SpecialistRunStatus.FAILED for result in results):
        return SpecialistExecutionStatus.FAILED
    return SpecialistExecutionStatus.BLOCKED
