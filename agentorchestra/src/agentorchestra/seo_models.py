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
    AgentOrchestraModel,
    EditRequest,
)
from agentorchestra.path_safety import reject_absolute_path_text, validate_relative_site_path


class SEOExecutionMode(StrEnum):
    EDIT = "edit"
    DIAGNOSTIC = "diagnostic"


class SEOFindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SEOFinding(AgentOrchestraModel):
    code: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    severity: SEOFindingSeverity
    title: StrictStr = Field(min_length=1, max_length=160)
    source_file: StrictStr = Field(min_length=1, max_length=120)
    evidence: StrictStr = Field(min_length=1, max_length=1_000)
    recommendation: StrictStr = Field(min_length=1, max_length=1_000)

    @field_validator("title", "evidence", "recommendation", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_file")
    @classmethod
    def validate_source_file(cls, value: str) -> str:
        value = validate_relative_site_path(value)
        return EditRequest(target_page=value, instruction="Validate SEO source file.").target_page

    @model_validator(mode="after")
    def reject_unsafe_text(self) -> SEOFinding:
        for value in (self.title, self.evidence, self.recommendation):
            reject_absolute_path_text(value, message="SEO findings must not expose absolute paths.")
        return self


class SEOCompletionStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"


class SEOCompletion(AgentOrchestraModel):
    mode: SEOExecutionMode
    status: SEOCompletionStatus
    summary: StrictStr = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    remaining_issue: StrictStr | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    findings: list[SEOFinding] = Field(default_factory=list)

    @field_validator("summary", "remaining_issue", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_mode_contract(self) -> SEOCompletion:
        if self.status is SEOCompletionStatus.BLOCKED:
            if not self.remaining_issue or self.findings:
                raise ValueError("blocked SEO completion requires one issue and no findings.")
            return self
        if self.remaining_issue is not None:
            raise ValueError("completed SEO output must not include a remaining issue.")
        if self.mode is SEOExecutionMode.EDIT and self.findings:
            raise ValueError("SEO edit completion must not include diagnostic findings.")
        if self.mode is SEOExecutionMode.DIAGNOSTIC and not self.findings:
            raise ValueError("SEO diagnostic completion requires non-empty findings.")
        codes = [finding.code for finding in self.findings]
        if len(codes) != len(set(codes)):
            raise ValueError("SEO diagnostic finding codes must be unique.")
        return self


class LighthouseRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LighthouseAuditStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INFORMATIVE = "informative"
    NOT_APPLICABLE = "not_applicable"


class LighthouseAuditItem(AgentOrchestraModel):
    audit_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
    title: StrictStr = Field(min_length=1, max_length=300)
    status: LighthouseAuditStatus
    score: StrictInt | None = Field(default=None, ge=0, le=100)
    display_value: StrictStr | None = Field(default=None, max_length=500)

    @field_validator("title", "display_value", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def reject_unsafe_text(self) -> LighthouseAuditItem:
        for value in (self.title, self.display_value):
            if value is not None:
                reject_absolute_path_text(
                    value,
                    message="Lighthouse audit evidence must not expose absolute paths.",
                )
        return self


class LighthouseSEOResult(AgentOrchestraModel):
    status: LighthouseRunStatus
    run_id: StrictStr = Field(min_length=1, max_length=80)
    target_page: StrictStr = Field(min_length=1, max_length=120)
    score: StrictInt | None = Field(default=None, ge=0, le=100)
    audits: list[LighthouseAuditItem] = Field(default_factory=list)
    failed_audit_ids: list[StrictStr] = Field(default_factory=list)
    report_path: StrictStr | None = Field(default=None, max_length=240)
    latency_ms: StrictFloat = Field(ge=0)
    error: StrictStr | None = Field(default=None, max_length=1_000)

    @field_validator("target_page")
    @classmethod
    def validate_target_page(cls, value: str) -> str:
        return EditRequest(target_page=value, instruction="Validate audit target.").target_page

    @field_validator("report_path")
    @classmethod
    def validate_report_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = validate_relative_site_path(value)
        if not normalized.startswith("reports/lighthouse/") or not normalized.endswith(".json"):
            raise ValueError("Lighthouse report path must be under reports/lighthouse.")
        return normalized

    @model_validator(mode="after")
    def validate_result(self) -> LighthouseSEOResult:
        audit_ids = [audit.audit_id for audit in self.audits]
        if audit_ids != sorted(audit_ids) or len(audit_ids) != len(set(audit_ids)):
            raise ValueError("Lighthouse audits must use unique deterministic ID order.")
        expected_failed = sorted(
            audit.audit_id for audit in self.audits if audit.status is LighthouseAuditStatus.FAILED
        )
        if self.failed_audit_ids != expected_failed:
            raise ValueError("failed_audit_ids must match failed audits in sorted order.")
        if self.status is LighthouseRunStatus.SUCCEEDED:
            if (
                self.score is None
                or not self.audits
                or self.report_path is None
                or self.error is not None
            ):
                raise ValueError(
                    "successful Lighthouse result requires score, audits, and report path."
                )
        elif self.score is not None or self.audits or self.failed_audit_ids or not self.error:
            raise ValueError("failed Lighthouse result must contain only safe failure evidence.")
        if self.error:
            reject_absolute_path_text(
                self.error,
                message="Lighthouse errors must not expose absolute paths.",
            )
        return self


class SEODiagnosticReport(AgentOrchestraModel):
    run_id: StrictStr = Field(min_length=1, max_length=80)
    target_page: StrictStr = Field(min_length=1, max_length=120)
    findings: list[SEOFinding] = Field(min_length=1)
    lighthouse: LighthouseSEOResult
    source_unchanged: StrictBool = True

    @model_validator(mode="after")
    def validate_diagnostic(self) -> SEODiagnosticReport:
        EditRequest(target_page=self.target_page, instruction="Validate diagnostic target.")
        if self.lighthouse.status is not LighthouseRunStatus.SUCCEEDED:
            raise ValueError("SEO diagnostic report requires successful Lighthouse evidence.")
        if self.lighthouse.run_id != self.run_id or self.lighthouse.target_page != self.target_page:
            raise ValueError("SEO diagnostic and Lighthouse evidence must identify the same run.")
        if not self.source_unchanged:
            raise ValueError("SEO diagnostic report must confirm unchanged staged source.")
        if any(finding.source_file != self.target_page for finding in self.findings):
            raise ValueError("SEO diagnostic findings must reference only the selected page.")
        codes = [finding.code for finding in self.findings]
        if len(codes) != len(set(codes)):
            raise ValueError("SEO diagnostic finding codes must be unique.")
        return self
