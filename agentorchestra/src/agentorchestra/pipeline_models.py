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
    AgentOrchestraModel,
    EditRequest,
    ManagerRoutingPlan,
    ManagerRunResult,
    QAResult,
    QAVerdict,
    SpecialistAssignment,
    SpecialistName,
    TokenUsage,
)
from agentorchestra.specialist_models import (
    SpecialistExecutionReport,
    SpecialistExecutionStatus,
)
from agentorchestra.workspace_models import DiffReport


class QARunResult(AgentOrchestraModel):
    """Validated QA execution evidence without raw provider payloads."""

    result: QAResult
    latency_ms: StrictFloat = Field(ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    model: StrictStr = Field(min_length=1, max_length=160)
    evidence_digest: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("model", mode="before")
    @classmethod
    def strip_model(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class EditOutcomeStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CLARIFICATION_REQUIRED = "clarification_required"
    OUT_OF_SCOPE = "out_of_scope"
    UNSUPPORTED_SPECIALIST = "unsupported_specialist"
    BLOCKED = "blocked"
    FAILED = "failed"


class QAPatchEvidence(AgentOrchestraModel):
    file: StrictStr = Field(min_length=1, max_length=120)
    specialist: StrictStr = Field(min_length=1, max_length=20)
    status: StrictStr = Field(min_length=1, max_length=20)
    summary: StrictStr = Field(min_length=1, max_length=300)
    match_count: StrictInt | None = Field(default=None, ge=0)
    replacements: StrictInt = Field(ge=0, le=1)
    before_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    after_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rejection_reason: StrictStr | None = Field(default=None, max_length=80)
    message: StrictStr = Field(min_length=1, max_length=500)


class QASpecialistEvidence(AgentOrchestraModel):
    specialist: SpecialistName
    assignment: StrictStr = Field(min_length=1, max_length=500)
    runtime_status: StrictStr = Field(min_length=1, max_length=40)
    completion_status: StrictStr | None = Field(default=None, max_length=40)
    completion_summary: StrictStr | None = Field(default=None, max_length=300)
    changed_files: list[StrictStr] = Field(default_factory=list)
    applied_patch_count: StrictInt = Field(ge=0)
    rejected_patch_count: StrictInt = Field(ge=0)
    patch_results: list[QAPatchEvidence] = Field(default_factory=list)


class QAEvidenceBundle(AgentOrchestraModel):
    request: EditRequest
    run_id: StrictStr = Field(min_length=1, max_length=80)
    target_page: StrictStr = Field(min_length=1, max_length=120)
    selected_specialists: list[SpecialistName]
    assignments: list[SpecialistAssignment]
    acceptance_criteria: list[StrictStr] = Field(min_length=1)
    specialist_results: list[QASpecialistEvidence] = Field(min_length=1)
    changed_files: list[StrictStr] = Field(default_factory=list)
    file_diffs: list[dict[str, object]] = Field(default_factory=list)
    combined_diff: StrictStr
    total_added_lines: StrictInt = Field(ge=0)
    total_removed_lines: StrictInt = Field(ge=0)
    evidence_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_bundle(self) -> QAEvidenceBundle:
        if self.target_page != self.request.target_page:
            raise ValueError("target_page must match the original request.")
        if [assignment.agent for assignment in self.assignments] != self.selected_specialists:
            raise ValueError("assignments must follow selected specialist order.")
        if self.changed_files != sorted(self.changed_files):
            raise ValueError("changed_files must be deterministic and sorted.")
        _reject_absolute_path_text(self.model_dump(mode="json"))
        return self


class PromotionResult(AgentOrchestraModel):
    run_id: StrictStr = Field(min_length=1, max_length=80)
    working_updated: StrictBool
    reviewed_diff: DiffReport
    final_diff: DiffReport
    candidate_cleaned: StrictBool
    backup_cleaned: StrictBool


class ResetResult(AgentOrchestraModel):
    working_reset: StrictBool
    working_matches_fixture: StrictBool
    candidate_cleaned: StrictBool
    backup_cleaned: StrictBool


class EditRunReport(AgentOrchestraModel):
    request: EditRequest
    status: EditOutcomeStatus
    manager_result: ManagerRunResult | None = None
    plan: ManagerRoutingPlan | None = None
    run_id: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    specialist_report: SpecialistExecutionReport | None = None
    qa_run: QARunResult | None = None
    reviewed_diff: DiffReport | None = None
    final_diff: DiffReport | None = None
    working_updated: StrictBool = False
    staging_cleaned: StrictBool = False
    message: StrictStr = Field(min_length=1, max_length=1_000)
    error: StrictStr | None = Field(default=None, max_length=1_000)
    total_latency_ms: StrictFloat = Field(ge=0)
    warnings: list[StrictStr] = Field(default_factory=list)

    @field_validator("message", "error", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_outcome(self) -> EditRunReport:
        if self.status is EditOutcomeStatus.ACCEPTED:
            self._validate_accepted()
        elif self.status is EditOutcomeStatus.REJECTED:
            self._validate_rejected()
        elif self.status in {
            EditOutcomeStatus.CLARIFICATION_REQUIRED,
            EditOutcomeStatus.OUT_OF_SCOPE,
            EditOutcomeStatus.UNSUPPORTED_SPECIALIST,
        }:
            self._validate_no_execution()
        elif self.status is EditOutcomeStatus.BLOCKED:
            self._validate_blocked()
        elif self.status is EditOutcomeStatus.FAILED and not self.error:
            raise ValueError("failed outcomes require an error.")
        if self.status is not EditOutcomeStatus.FAILED and self.error is not None:
            raise ValueError("non-failed outcomes must not include an error.")
        _reject_absolute_path_text(self.model_dump(mode="json"))
        return self

    def _validate_accepted(self) -> None:
        if self.manager_result is None or self.plan is None or not self.run_id:
            raise ValueError("accepted outcomes require manager result, plan, and run ID.")
        if self.specialist_report is None or self.specialist_report.status is not SpecialistExecutionStatus.SUCCEEDED:
            raise ValueError("accepted outcomes require successful specialist execution.")
        if self.qa_run is None or self.qa_run.result.verdict is not QAVerdict.ACCEPT:
            raise ValueError("accepted outcomes require an accepting QA run.")
        if self.reviewed_diff is None or self.final_diff is None or self.reviewed_diff != self.final_diff:
            raise ValueError("accepted outcomes require identical reviewed and final diffs.")
        if self.reviewed_diff.is_empty:
            raise ValueError("accepted outcomes require a non-empty diff.")
        if not self.working_updated or not self.staging_cleaned:
            raise ValueError("accepted outcomes require working update and staging cleanup.")

    def _validate_rejected(self) -> None:
        if self.manager_result is None or self.plan is None:
            raise ValueError("rejected outcomes require manager result and plan.")
        if self.specialist_report is None or self.specialist_report.status is not SpecialistExecutionStatus.SUCCEEDED:
            raise ValueError("rejected outcomes require successful specialist execution.")
        if self.qa_run is None or self.qa_run.result.verdict is not QAVerdict.REJECT:
            raise ValueError("rejected outcomes require a rejecting QA run.")
        if self.working_updated or not self.staging_cleaned:
            raise ValueError("rejected outcomes must leave working unchanged and clean staging.")

    def _validate_no_execution(self) -> None:
        if self.run_id or self.specialist_report or self.qa_run or self.reviewed_diff or self.final_diff:
            raise ValueError("non-execution outcomes must not include execution artifacts.")
        if self.working_updated:
            raise ValueError("non-execution outcomes must not update working.")

    def _validate_blocked(self) -> None:
        if self.manager_result is None or self.plan is None or self.specialist_report is None:
            raise ValueError("blocked outcomes require manager, plan, and specialist evidence.")
        if self.qa_run is not None:
            raise ValueError("blocked outcomes must not include QA.")
        if self.working_updated or not self.staging_cleaned:
            raise ValueError("blocked outcomes must leave working unchanged and clean staging.")


def _reject_absolute_path_text(value: object) -> None:
    text = str(value)
    if ":\\" in text or ":/" in text:
        raise ValueError("user-facing evidence must not include absolute paths.")
