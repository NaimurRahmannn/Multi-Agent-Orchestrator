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
from agentorchestra.path_safety import reject_absolute_path_text, validate_relative_site_path
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
    site_content_digest: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

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

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        return validate_relative_site_path(value)


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

    @field_validator("changed_files")
    @classmethod
    def validate_changed_files(cls, value: list[str]) -> list[str]:
        return [validate_relative_site_path(item) for item in value]


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
    site_content_digest: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_bundle(self) -> QAEvidenceBundle:
        if self.target_page != self.request.target_page:
            raise ValueError("target_page must match the original request.")
        if [assignment.agent for assignment in self.assignments] != self.selected_specialists:
            raise ValueError("assignments must follow selected specialist order.")
        if self.changed_files != sorted(self.changed_files):
            raise ValueError("changed_files must be deterministic and sorted.")
        validate_relative_site_path(self.target_page)
        for file in self.changed_files:
            validate_relative_site_path(file)
        for file_diff in self.file_diffs:
            file = file_diff.get("file")
            if not isinstance(file, str):
                raise ValueError("file_diffs must include a relative file path.")
            validate_relative_site_path(file)
        free_form_values = [
            self.request.instruction,
            *self.acceptance_criteria,
            *(assignment.task for assignment in self.assignments),
        ]
        for result in self.specialist_results:
            free_form_values.extend(
                value
                for value in (
                    result.assignment,
                    result.completion_summary,
                    *(patch.summary for patch in result.patch_results),
                    *(patch.message for patch in result.patch_results),
                )
                if value is not None
            )
        for value in free_form_values:
            reject_absolute_path_text(
                value,
                message="user-facing evidence must not include absolute paths.",
            )
        return self


class SiteTreeDigest(AgentOrchestraModel):
    digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[StrictStr] = Field(min_length=1)
    total_bytes: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_files(self) -> SiteTreeDigest:
        if self.files != sorted(self.files) or len(set(self.files)) != len(self.files):
            raise ValueError("digest files must be unique and sorted.")
        for file in self.files:
            validate_relative_site_path(file)
        return self


class PromotionStatus(StrEnum):
    COMMITTED = "committed"
    COMMITTED_WITH_WARNING = "committed_with_warning"
    ROLLED_BACK = "rolled_back"


class PromotionResult(AgentOrchestraModel):
    run_id: StrictStr = Field(min_length=1, max_length=80)
    status: PromotionStatus
    working_updated: StrictBool
    working_restored: StrictBool = False
    reviewed_diff: DiffReport
    final_diff: DiffReport
    staging_cleaned: StrictBool
    candidate_cleaned: StrictBool
    backup_cleaned: StrictBool
    accepted_content_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    final_working_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[StrictStr] = Field(default_factory=list)
    message: StrictStr = Field(min_length=1, max_length=1_000)
    recovery_required: StrictBool = False

    @model_validator(mode="after")
    def validate_commit(self) -> PromotionResult:
        if self.status is PromotionStatus.ROLLED_BACK:
            if self.working_updated or not self.working_restored:
                raise ValueError("rolled-back promotion must restore working without updating it.")
            return self
        if not self.working_updated or self.working_restored or self.recovery_required:
            raise ValueError("committed promotion must prove a safe working update.")
        if self.reviewed_diff != self.final_diff or self.reviewed_diff.is_empty:
            raise ValueError("committed promotion requires an unchanged non-empty reviewed diff.")
        if self.accepted_content_digest != self.final_working_digest:
            raise ValueError("committed promotion digests must match.")
        cleanup_clean = self.staging_cleaned and self.candidate_cleaned and self.backup_cleaned
        if self.status is PromotionStatus.COMMITTED and (not cleanup_clean or self.warnings):
            raise ValueError("clean committed promotion cannot include cleanup warnings.")
        if self.status is PromotionStatus.COMMITTED_WITH_WARNING and (
            cleanup_clean or not self.warnings
        ):
            raise ValueError("committed-with-warning promotion requires an honest cleanup warning.")
        return self


class ResetResult(AgentOrchestraModel):
    status: PromotionStatus
    working_reset: StrictBool
    working_matches_fixture: StrictBool
    working_restored: StrictBool = False
    candidate_cleaned: StrictBool
    backup_cleaned: StrictBool
    fixture_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    final_working_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[StrictStr] = Field(default_factory=list)
    message: StrictStr = Field(min_length=1, max_length=1_000)
    recovery_required: StrictBool = False

    @model_validator(mode="after")
    def validate_reset(self) -> ResetResult:
        if not self.working_reset or not self.working_matches_fixture:
            raise ValueError("successful reset results must match fixture.")
        if self.working_restored or self.recovery_required:
            raise ValueError("successful reset results cannot require recovery.")
        if self.fixture_digest != self.final_working_digest:
            raise ValueError("successful reset digests must match.")
        cleanup_clean = self.candidate_cleaned and self.backup_cleaned
        if self.status is PromotionStatus.COMMITTED and (not cleanup_clean or self.warnings):
            raise ValueError("clean reset cannot include cleanup warnings.")
        if self.status is PromotionStatus.COMMITTED_WITH_WARNING and (
            cleanup_clean or not self.warnings
        ):
            raise ValueError("reset cleanup warning state is contradictory.")
        if self.status is PromotionStatus.ROLLED_BACK:
            raise ValueError("successful reset result cannot be rolled back.")
        return self


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
    promotion_result: PromotionResult | None = None
    promotion_status: PromotionStatus | None = None
    accepted_content_digest: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    final_working_digest: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    working_updated: StrictBool = False
    working_restored: StrictBool = False
    staging_cleaned: StrictBool = False
    message: StrictStr = Field(min_length=1, max_length=1_000)
    error: StrictStr | None = Field(default=None, max_length=1_000)
    total_latency_ms: StrictFloat = Field(ge=0)
    warnings: list[StrictStr] = Field(default_factory=list)
    cleanup_warnings: list[StrictStr] = Field(default_factory=list)
    recovery_required: StrictBool = False

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
        for value in [self.message, self.error, *self.warnings, *self.cleanup_warnings]:
            if value is not None:
                reject_absolute_path_text(
                    value,
                    message="user-facing evidence must not include absolute paths.",
                )
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
        if self.promotion_result is None or self.promotion_status is None:
            raise ValueError("accepted outcomes require promotion evidence.")
        if self.promotion_status not in {
            PromotionStatus.COMMITTED,
            PromotionStatus.COMMITTED_WITH_WARNING,
        }:
            raise ValueError("accepted outcomes require a committed promotion.")
        if self.promotion_result.status is not self.promotion_status:
            raise ValueError("promotion status must match the promotion result.")
        if not self.working_updated or self.working_restored or self.recovery_required:
            raise ValueError("accepted outcomes require a proven working update.")
        if (
            self.accepted_content_digest is None
            or self.accepted_content_digest != self.final_working_digest
            or self.accepted_content_digest != self.promotion_result.accepted_content_digest
        ):
            raise ValueError("accepted outcomes require matching content digests.")
        if self.staging_cleaned != self.promotion_result.staging_cleaned:
            raise ValueError("accepted staging cleanup state must be honest.")
        if self.cleanup_warnings != self.promotion_result.warnings:
            raise ValueError("accepted cleanup warnings must match promotion evidence.")

    def _validate_rejected(self) -> None:
        if self.manager_result is None or self.plan is None:
            raise ValueError("rejected outcomes require manager result and plan.")
        if self.specialist_report is None or self.specialist_report.status is not SpecialistExecutionStatus.SUCCEEDED:
            raise ValueError("rejected outcomes require successful specialist execution.")
        if self.qa_run is None or self.qa_run.result.verdict is not QAVerdict.REJECT:
            raise ValueError("rejected outcomes require a rejecting QA run.")
        if self.working_updated:
            raise ValueError("rejected outcomes must leave working unchanged.")
        if not self.staging_cleaned and not self.cleanup_warnings:
            raise ValueError("unclean rejected outcomes must include a cleanup warning.")

    def _validate_no_execution(self) -> None:
        if (
            self.run_id
            or self.specialist_report
            or self.qa_run
            or self.reviewed_diff
            or self.final_diff
            or self.promotion_result
        ):
            raise ValueError("non-execution outcomes must not include execution artifacts.")
        if self.working_updated:
            raise ValueError("non-execution outcomes must not update working.")

    def _validate_blocked(self) -> None:
        if self.manager_result is None or self.plan is None or self.specialist_report is None:
            raise ValueError("blocked outcomes require manager, plan, and specialist evidence.")
        if self.qa_run is not None:
            raise ValueError("blocked outcomes must not include QA.")
        if self.working_updated:
            raise ValueError("blocked outcomes must leave working unchanged.")
        if not self.staging_cleaned and not self.cleanup_warnings:
            raise ValueError("unclean blocked outcomes must include a cleanup warning.")
