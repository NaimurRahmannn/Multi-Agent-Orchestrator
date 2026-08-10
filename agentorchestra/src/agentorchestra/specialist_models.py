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
    SEO_DIAGNOSTIC_REQUEST_TYPE,
    AgentOrchestraModel,
    EditRequest,
    ManagerRoutingPlan,
    RoutingStatus,
    SpecialistName,
    TokenUsage,
)
from agentorchestra.seo_models import SEOCompletion, SEOCompletionStatus, SEOExecutionMode
from agentorchestra.style_models import StyleChangeEvidence, StyleIntentPlan
from agentorchestra.workspace_models import DiffReport, PatchExecutionResult, PatchStatus


class SpecialistCompletionStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CLARIFICATION_REQUIRED = "clarification_required"
    ALREADY_SATISFIED = "already_satisfied"


class SpecialistCompletion(AgentOrchestraModel):
    """A specialist's concise statement, separate from authoritative patch evidence."""

    status: SpecialistCompletionStatus
    summary: StrictStr = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    remaining_issue: StrictStr | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    clarification_question: StrictStr | None = Field(default=None, max_length=MAX_REASON_LENGTH)

    @field_validator("summary", "remaining_issue", "clarification_question", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_status_contract(self) -> SpecialistCompletion:
        if self.status in {
            SpecialistCompletionStatus.COMPLETED,
            SpecialistCompletionStatus.ALREADY_SATISFIED,
        }:
            if self.remaining_issue is not None or self.clarification_question is not None:
                raise ValueError("successful specialist output cannot include an issue or question.")
        elif self.status is SpecialistCompletionStatus.BLOCKED:
            if not self.remaining_issue or self.clarification_question is not None:
                raise ValueError("blocked specialist output requires only a remaining issue.")
        elif not self.clarification_question or self.remaining_issue is not None:
            raise ValueError("clarification specialist output requires only one question.")
        return self


class SpecialistRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_SATISFIED = "already_satisfied"
    CLARIFICATION_REQUIRED = "clarification_required"
    BLOCKED = "blocked"
    FAILED = "failed"


class SpecialistRunResult(AgentOrchestraModel):
    specialist: SpecialistName
    mode: SEOExecutionMode = SEOExecutionMode.EDIT
    assignment: StrictStr = Field(min_length=1, max_length=MAX_TASK_LENGTH)
    status: SpecialistRunStatus
    completion: SpecialistCompletion | SEOCompletion | None = None
    patch_results: list[PatchExecutionResult] = Field(default_factory=list)
    changed_files: list[StrictStr] = Field(default_factory=list)
    applied_patch_count: StrictInt = Field(ge=0)
    rejected_patch_count: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    model: StrictStr = Field(min_length=1, max_length=160)
    error: StrictStr | None = Field(default=None, max_length=1_000)
    style_plan: StyleIntentPlan | None = None
    style_changes: list[StyleChangeEvidence] = Field(default_factory=list)

    @field_validator("assignment", "model", "error", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_runtime_evidence(self) -> SpecialistRunResult:
        if self.specialist in {SpecialistName.HTML, SpecialistName.CSS}:
            if self.mode is not SEOExecutionMode.EDIT or isinstance(self.completion, SEOCompletion):
                raise ValueError("HTML and CSS runs support edit completion only.")
        elif self.specialist is SpecialistName.SEO:
            if self.completion is not None and not isinstance(self.completion, SEOCompletion):
                raise ValueError("SEO runs require the SEO completion contract.")
            if isinstance(self.completion, SEOCompletion) and self.completion.mode is not self.mode:
                raise ValueError("SEO completion mode must match the runtime mode.")
        else:
            raise ValueError("unsupported specialist run.")
        if self.specialist is not SpecialistName.CSS and (self.style_plan or self.style_changes):
            raise ValueError("Only CSS runs may contain semantic style evidence.")

        applied = [result for result in self.patch_results if result.status is PatchStatus.APPLIED]
        rejected = [
            result for result in self.patch_results if result.status is PatchStatus.REJECTED
        ]
        if self.applied_patch_count != len(applied):
            raise ValueError("applied_patch_count must match patch_results.")
        if self.rejected_patch_count != len(rejected):
            raise ValueError("rejected_patch_count must match patch_results.")

        evidence_files = sorted({result.file for result in applied})
        if self.changed_files != evidence_files:
            raise ValueError(
                "changed_files must be unique, sorted, and derived from applied patches."
            )
        if any(result.specialist != self.specialist.value for result in self.patch_results):
            raise ValueError("patch evidence specialist must match the run specialist.")
        if self.mode is SEOExecutionMode.DIAGNOSTIC and self.patch_results:
            raise ValueError("SEO diagnostic runs must not include patch evidence.")

        if self.status is SpecialistRunStatus.SUCCEEDED:
            if self.error is not None:
                raise ValueError("succeeded runs must not include an error.")
            if self.mode is SEOExecutionMode.DIAGNOSTIC:
                if applied or not isinstance(self.completion, SEOCompletion):
                    raise ValueError("successful SEO diagnosis requires findings without patches.")
                if self.completion.status is not SEOCompletionStatus.COMPLETED:
                    raise ValueError("successful SEO diagnosis requires completed findings.")
            else:
                if not applied or self.applied_patch_count == 0:
                    raise ValueError("succeeded edit runs require an applied patch.")
                if self.completion is None or self.completion.status.value != "completed":
                    raise ValueError("succeeded edit runs require a completed completion.")
        elif self.status is SpecialistRunStatus.BLOCKED:
            if applied or self.applied_patch_count:
                raise ValueError("blocked runs must not include an applied patch.")
            if self.error is not None:
                raise ValueError("blocked runs must not include an error.")
            if self.completion is None or self.completion.status.value != "blocked":
                raise ValueError("blocked runs require a blocked specialist completion.")
        elif self.status is SpecialistRunStatus.CLARIFICATION_REQUIRED:
            if applied or self.applied_patch_count or self.error is not None:
                raise ValueError("clarification runs cannot apply patches or include errors.")
            if (
                self.completion is None
                or self.completion.status.value != "clarification_required"
            ):
                raise ValueError("clarification runs require a clarification completion.")
        elif self.status is SpecialistRunStatus.ALREADY_SATISFIED:
            if applied or self.patch_results or self.error is not None:
                raise ValueError("already-satisfied runs cannot include patches or errors.")
            if self.completion is None or self.completion.status.value != "already_satisfied":
                raise ValueError("already-satisfied runs require matching completion evidence.")
            if self.specialist is SpecialistName.CSS and not self.style_changes:
                raise ValueError("already-satisfied CSS runs require style evidence.")
        elif not self.error:
            raise ValueError("failed runs must include an error.")
        return self


class SpecialistExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_SATISFIED = "already_satisfied"
    CLARIFICATION_REQUIRED = "clarification_required"
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
    seo_mode: SEOExecutionMode | None = None

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> SpecialistExecutionReport:
        if self.plan.status is not RoutingStatus.EXECUTE:
            raise ValueError("specialist execution reports require an execute plan.")
        if self.run_id != self.diff_report.run_id:
            raise ValueError("run_id must match the final diff report.")
        selected = self.plan.selected_specialists
        if any(
            specialist not in {SpecialistName.HTML, SpecialistName.CSS, SpecialistName.SEO}
            for specialist in selected
        ):
            raise ValueError("execution report contains an unsupported specialist.")
        expected_seo_mode = None
        if SpecialistName.SEO in selected:
            expected_seo_mode = (
                SEOExecutionMode.DIAGNOSTIC
                if self.plan.request_type == SEO_DIAGNOSTIC_REQUEST_TYPE
                else SEOExecutionMode.EDIT
            )
        if self.seo_mode is not expected_seo_mode:
            raise ValueError("seo_mode must match the validated Manager plan.")
        result_specialists = [result.specialist for result in self.results]
        if result_specialists != selected[: len(result_specialists)]:
            raise ValueError("result order must be the executed prefix of Manager plan order.")
        if len(result_specialists) > len(selected):
            raise ValueError("execution report contains an unselected specialist.")
        if self.stopped_early != (len(result_specialists) < len(selected)):
            raise ValueError("stopped_early must reflect omitted later specialists.")

        expected_status = _execution_status(
            self.results, all_selected=len(self.results) == len(selected)
        )
        if self.status is not expected_status:
            raise ValueError("execution status must be derived from specialist run results.")
        expected_latency = sum(result.latency_ms for result in self.results)
        if abs(self.total_latency_ms - expected_latency) > 0.001:
            raise ValueError("total_latency_ms must equal the specialist latency total.")
        if self.status is SpecialistExecutionStatus.SUCCEEDED:
            if self.seo_mode is SEOExecutionMode.DIAGNOSTIC and not self.diff_report.is_empty:
                raise ValueError("successful SEO diagnosis must leave the staged diff empty.")
            if self.seo_mode is not SEOExecutionMode.DIAGNOSTIC and self.diff_report.is_empty:
                raise ValueError("successful edit execution requires a non-empty staged diff.")
        return self


def _execution_status(
    results: list[SpecialistRunResult], *, all_selected: bool
) -> SpecialistExecutionStatus:
    statuses = [result.status for result in results]
    succeeded = sum(status is SpecialistRunStatus.SUCCEEDED for status in statuses)
    satisfied = sum(
        status in {
            SpecialistRunStatus.SUCCEEDED,
            SpecialistRunStatus.ALREADY_SATISFIED,
        }
        for status in statuses
    )
    if succeeded and satisfied == len(statuses) and all_selected:
        return SpecialistExecutionStatus.SUCCEEDED
    if any(status is SpecialistRunStatus.CLARIFICATION_REQUIRED for status in statuses):
        return SpecialistExecutionStatus.CLARIFICATION_REQUIRED
    if (
        statuses
        and all(status is SpecialistRunStatus.ALREADY_SATISFIED for status in statuses)
        and all_selected
    ):
        return SpecialistExecutionStatus.ALREADY_SATISFIED
    if succeeded:
        return SpecialistExecutionStatus.PARTIAL
    if any(status is SpecialistRunStatus.FAILED for status in statuses):
        return SpecialistExecutionStatus.FAILED
    return SpecialistExecutionStatus.BLOCKED
