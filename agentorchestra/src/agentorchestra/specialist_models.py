from __future__ import annotations

from enum import StrEnum

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from agentorchestra.models import (
    MAX_REASON_LENGTH,
    MAX_SUMMARY_LENGTH,
    MAX_TASK_LENGTH,
    AgentOrchestraModel,
    EditRequest,
    ManagerRoutingPlan,
    RoutingStatus,
    SpecialistName,
    TokenUsage,
)
from agentorchestra.workspace_models import DiffReport, PatchExecutionResult, PatchStatus


class SpecialistCompletionStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"


class SpecialistCompletion(AgentOrchestraModel):
    """A specialist's concise statement, separate from authoritative patch evidence."""

    status: SpecialistCompletionStatus
    summary: StrictStr = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    remaining_issue: StrictStr | None = Field(default=None, max_length=MAX_REASON_LENGTH)

    @field_validator("summary", "remaining_issue", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_status_contract(self) -> SpecialistCompletion:
        if self.status is SpecialistCompletionStatus.COMPLETED and self.remaining_issue is not None:
            raise ValueError("completed specialist output must not include a remaining issue.")
        if self.status is SpecialistCompletionStatus.BLOCKED and not self.remaining_issue:
            raise ValueError("blocked specialist output must include a remaining issue.")
        return self


class SpecialistRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class SpecialistRunResult(AgentOrchestraModel):
    specialist: SpecialistName
    assignment: StrictStr = Field(min_length=1, max_length=MAX_TASK_LENGTH)
    status: SpecialistRunStatus
    completion: SpecialistCompletion | None = None
    patch_results: list[PatchExecutionResult] = Field(default_factory=list)
    changed_files: list[StrictStr] = Field(default_factory=list)
    applied_patch_count: StrictInt = Field(ge=0)
    rejected_patch_count: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    model: StrictStr = Field(min_length=1, max_length=160)
    error: StrictStr | None = Field(default=None, max_length=1_000)

    @field_validator("assignment", "model", "error", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_runtime_evidence(self) -> SpecialistRunResult:
        if self.specialist not in {SpecialistName.HTML, SpecialistName.CSS}:
            raise ValueError("specialist runs in this stage support only HTML and CSS.")

        applied = [result for result in self.patch_results if result.status is PatchStatus.APPLIED]
        rejected = [result for result in self.patch_results if result.status is PatchStatus.REJECTED]
        if self.applied_patch_count != len(applied):
            raise ValueError("applied_patch_count must match patch_results.")
        if self.rejected_patch_count != len(rejected):
            raise ValueError("rejected_patch_count must match patch_results.")

        evidence_files = sorted({result.file for result in applied})
        if self.changed_files != evidence_files:
            raise ValueError("changed_files must be unique, sorted, and derived from applied patches.")
        if any(result.specialist != self.specialist.value for result in self.patch_results):
            raise ValueError("patch evidence specialist must match the run specialist.")

        if self.status is SpecialistRunStatus.SUCCEEDED:
            if not applied or self.applied_patch_count == 0:
                raise ValueError("succeeded runs require an applied patch.")
            if self.error is not None:
                raise ValueError("succeeded runs must not include an error.")
            if self.completion is None or self.completion.status is not SpecialistCompletionStatus.COMPLETED:
                raise ValueError("succeeded runs require a completed specialist completion.")
        elif self.status is SpecialistRunStatus.BLOCKED:
            if applied or self.applied_patch_count:
                raise ValueError("blocked runs must not include an applied patch.")
            if self.error is not None:
                raise ValueError("blocked runs must not include an error.")
            if self.completion is None or self.completion.status is not SpecialistCompletionStatus.BLOCKED:
                raise ValueError("blocked runs require a blocked specialist completion.")
        elif not self.error:
            raise ValueError("failed runs must include an error.")
        return self


class SpecialistExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class SpecialistExecutionReport(AgentOrchestraModel):
    run_id: StrictStr = Field(min_length=1, max_length=80)
    request: EditRequest
    plan: ManagerRoutingPlan
    status: SpecialistExecutionStatus
    results: list[SpecialistRunResult] = Field(min_length=1)
    diff_report: DiffReport
    total_latency_ms: StrictFloat = Field(ge=0)
    stopped_early: StrictBool = False

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> SpecialistExecutionReport:
        if self.plan.status is not RoutingStatus.EXECUTE:
            raise ValueError("specialist execution reports require an execute plan.")
        if self.run_id != self.diff_report.run_id:
            raise ValueError("run_id must match the final diff report.")
        selected = self.plan.selected_specialists
        if any(specialist not in {SpecialistName.HTML, SpecialistName.CSS} for specialist in selected):
            raise ValueError("execution reports in this stage support only HTML and CSS.")
        result_specialists = [result.specialist for result in self.results]
        if result_specialists != selected[: len(result_specialists)]:
            raise ValueError("result order must be the executed prefix of Manager plan order.")
        if len(result_specialists) > len(selected):
            raise ValueError("execution report contains an unselected specialist.")
        if self.stopped_early != (len(result_specialists) < len(selected)):
            raise ValueError("stopped_early must reflect omitted later specialists.")

        expected_status = _execution_status(self.results, all_selected=len(self.results) == len(selected))
        if self.status is not expected_status:
            raise ValueError("execution status must be derived from specialist run results.")
        expected_latency = sum(result.latency_ms for result in self.results)
        if abs(self.total_latency_ms - expected_latency) > 0.001:
            raise ValueError("total_latency_ms must equal the specialist latency total.")
        return self


def _execution_status(
    results: list[SpecialistRunResult], *, all_selected: bool
) -> SpecialistExecutionStatus:
    statuses = [result.status for result in results]
    succeeded = sum(status is SpecialistRunStatus.SUCCEEDED for status in statuses)
    if succeeded and succeeded == len(statuses) and all_selected:
        return SpecialistExecutionStatus.SUCCEEDED
    if succeeded:
        return SpecialistExecutionStatus.PARTIAL
    if any(status is SpecialistRunStatus.FAILED for status in statuses):
        return SpecialistExecutionStatus.FAILED
    return SpecialistExecutionStatus.BLOCKED
