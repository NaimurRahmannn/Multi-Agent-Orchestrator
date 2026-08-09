from collections.abc import Sequence
from enum import StrEnum
from pathlib import PurePath
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from agentorchestra.exceptions import QACoverageError

MAX_TASK_LENGTH = 500
MAX_REQUEST_TYPE_LENGTH = 80
MAX_RATIONALE_LENGTH = 500
MAX_CRITERION_LENGTH = 300
MAX_INSTRUCTION_LENGTH = 2_000
MAX_PATCH_TEXT_LENGTH = 5_000
MAX_SUMMARY_LENGTH = 300
MAX_EVIDENCE_LENGTH = 1_000
MAX_REASON_LENGTH = 1_000
SEO_EDIT_REQUEST_TYPE = "seo_edit"
SEO_DIAGNOSTIC_REQUEST_TYPE = "seo_diagnostic"
HTML_EDIT_REQUEST_TYPE = "html_edit"
CSS_EDIT_REQUEST_TYPE = "css_edit"
HTML_CSS_EDIT_REQUEST_TYPE = "html_css_edit"


class AgentOrchestraModel(BaseModel):
    """Base for strict JSON-friendly AgentOrchestra domain contracts."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


class RoutingStatus(StrEnum):
    EXECUTE = "execute"
    CLARIFICATION_REQUIRED = "clarification_required"
    OUT_OF_SCOPE = "out_of_scope"


class SpecialistName(StrEnum):
    HTML = "html"
    CSS = "css"
    SEO = "seo"


class CriterionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class QAVerdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class SpecialistAssignment(AgentOrchestraModel):
    agent: SpecialistName
    task: StrictStr = Field(min_length=1, max_length=MAX_TASK_LENGTH)

    @field_validator("task", mode="before")
    @classmethod
    def strip_task(cls, value: object) -> object:
        return _strip_text(value)


class ManagerRoutingPlan(AgentOrchestraModel):
    status: RoutingStatus
    request_type: StrictStr = Field(min_length=1, max_length=MAX_REQUEST_TYPE_LENGTH)
    selected_specialists: list[SpecialistName] = Field(default_factory=list)
    routing_rationale: StrictStr = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    assignments: list[SpecialistAssignment] = Field(default_factory=list)
    acceptance_criteria: list[StrictStr] = Field(default_factory=list)
    clarification_question: StrictStr | None = Field(default=None, max_length=MAX_CRITERION_LENGTH)
    rejection_reason: StrictStr | None = Field(default=None, max_length=MAX_CRITERION_LENGTH)

    @field_validator(
        "request_type",
        "routing_rationale",
        "clarification_question",
        "rejection_reason",
        mode="before",
    )
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def strip_criteria(cls, value: object) -> object:
        return _strip_text_items(value)

    @model_validator(mode="after")
    def validate_status_invariants(self) -> "ManagerRoutingPlan":
        _reject_duplicates(self.selected_specialists, "selected_specialists")
        _reject_blank_items(self.acceptance_criteria, "acceptance_criteria")
        _reject_case_insensitive_duplicates(self.acceptance_criteria, "acceptance_criteria")
        _reject_overlong_items(
            self.acceptance_criteria,
            MAX_CRITERION_LENGTH,
            "acceptance_criteria",
        )

        assignment_agents = [assignment.agent for assignment in self.assignments]
        _reject_duplicates(assignment_agents, "assignments")

        if self.status is RoutingStatus.EXECUTE:
            self._validate_execute(assignment_agents)
        elif self.status is RoutingStatus.CLARIFICATION_REQUIRED:
            self._validate_clarification_required()
        elif self.status is RoutingStatus.OUT_OF_SCOPE:
            self._validate_out_of_scope()
        return self

    def _validate_execute(self, assignment_agents: list[SpecialistName]) -> None:
        if not self.selected_specialists:
            raise ValueError("execute plans must select at least one specialist.")
        if set(assignment_agents) != set(self.selected_specialists):
            raise ValueError(
                "execute plans must include exactly one assignment for each selected specialist."
            )
        self.assignments = _assignments_in_specialist_order(
            self.selected_specialists,
            self.assignments,
        )
        if not self.acceptance_criteria:
            raise ValueError("execute plans must include acceptance criteria.")
        if self.clarification_question:
            raise ValueError("execute plans must not include a clarification question.")
        if self.rejection_reason:
            raise ValueError("execute plans must not include a rejection reason.")
        seo_selected = SpecialistName.SEO in self.selected_specialists
        if self.request_type == SEO_DIAGNOSTIC_REQUEST_TYPE and self.selected_specialists != [
            SpecialistName.SEO
        ]:
            raise ValueError("seo_diagnostic plans must select only the SEO specialist.")
        expected_request_type = _expected_execute_request_type(
            self.selected_specialists,
            requested=self.request_type,
        )
        if self.request_type != expected_request_type:
            raise ValueError(
                "execute request_type must match the selected specialist ownership."
            )
        if seo_selected and self.request_type not in {
            SEO_EDIT_REQUEST_TYPE,
            SEO_DIAGNOSTIC_REQUEST_TYPE,
        }:
            raise ValueError("SEO plans must use request_type seo_edit or seo_diagnostic.")

    def _validate_clarification_required(self) -> None:
        if self.selected_specialists or self.assignments or self.acceptance_criteria:
            raise ValueError("clarification plans must not include execution fields.")
        if not self.clarification_question:
            raise ValueError("clarification plans must include a clarification question.")
        if self.rejection_reason:
            raise ValueError("clarification plans must not include a rejection reason.")

    def _validate_out_of_scope(self) -> None:
        if self.selected_specialists or self.assignments or self.acceptance_criteria:
            raise ValueError("out_of_scope plans must not include execution fields.")
        if not self.rejection_reason:
            raise ValueError("out_of_scope plans must include a rejection reason.")
        if self.clarification_question:
            raise ValueError("out_of_scope plans must not include a clarification question.")


class EditRequest(AgentOrchestraModel):
    target_page: StrictStr = Field(min_length=1, max_length=120)
    instruction: StrictStr = Field(min_length=1, max_length=MAX_INSTRUCTION_LENGTH)

    @field_validator("target_page", "instruction", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("target_page")
    @classmethod
    def validate_target_page(cls, value: str) -> str:
        _validate_simple_site_file(value, allowed_extensions={".html"})
        if value.startswith("."):
            raise ValueError("target_page must not be a hidden filename.")
        return value


class PatchProposal(AgentOrchestraModel):
    agent: SpecialistName
    file: StrictStr = Field(min_length=1, max_length=120)
    old_text: StrictStr = Field(min_length=1, max_length=MAX_PATCH_TEXT_LENGTH)
    new_text: StrictStr = Field(min_length=1, max_length=MAX_PATCH_TEXT_LENGTH)
    summary: StrictStr = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)

    @field_validator("file", "summary", mode="before")
    @classmethod
    def strip_safe_text_fields(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("old_text", "new_text")
    @classmethod
    def validate_patch_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("patch text must not contain null bytes.")
        return value

    @model_validator(mode="after")
    def validate_patch_contract(self) -> "PatchProposal":
        _validate_simple_site_file(self.file, allowed_extensions={".html", ".css"})
        if self.old_text == self.new_text:
            raise ValueError("old_text and new_text must differ.")
        suffix = PurePath(self.file).suffix
        if self.agent is SpecialistName.CSS and suffix != ".css":
            raise ValueError("CSS proposals may target only .css files.")
        if self.agent in {SpecialistName.HTML, SpecialistName.SEO} and suffix != ".html":
            raise ValueError("HTML and SEO proposals may target only .html files.")
        return self


class RoutingEvidenceCase(AgentOrchestraModel):
    case_id: StrictStr = Field(min_length=1, max_length=80)
    request: StrictStr = Field(min_length=1, max_length=MAX_INSTRUCTION_LENGTH)
    expected_status: RoutingStatus
    expected_specialists: list[SpecialistName] = Field(default_factory=list)

    @field_validator("case_id", "request", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        if not all(character.isalnum() or character in {"-", "_"} for character in value):
            raise ValueError("case_id may contain only letters, numbers, hyphens, and underscores.")
        return value

    @model_validator(mode="after")
    def validate_expected_route(self) -> "RoutingEvidenceCase":
        _reject_duplicates(self.expected_specialists, "expected_specialists")
        if self.expected_status is RoutingStatus.EXECUTE and not self.expected_specialists:
            raise ValueError("execute evidence cases must include expected specialists.")
        if self.expected_status is not RoutingStatus.EXECUTE and self.expected_specialists:
            raise ValueError("non-execute evidence cases must not include expected specialists.")
        return self


class TokenUsage(AgentOrchestraModel):
    prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    completion_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "TokenUsage":
        if (
            self.prompt_tokens is not None
            and self.completion_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens != self.prompt_tokens + self.completion_tokens
        ):
            raise ValueError("total_tokens must equal prompt_tokens plus completion_tokens.")
        return self


class RoutingEvidenceResult(AgentOrchestraModel):
    case_id: StrictStr = Field(min_length=1, max_length=80)
    request: StrictStr = Field(min_length=1, max_length=MAX_INSTRUCTION_LENGTH)
    expected_status: RoutingStatus
    expected_specialists: list[SpecialistName] = Field(default_factory=list)
    actual_status: RoutingStatus | None = None
    actual_specialists: list[SpecialistName] = Field(default_factory=list)
    routing_rationale: StrictStr | None = Field(default=None, max_length=MAX_RATIONALE_LENGTH)
    structurally_valid: StrictBool
    routing_correct: StrictBool
    latency_ms: StrictInt | None = Field(default=None, ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    validation_error: StrictStr | None = Field(default=None, max_length=1_000)

    @field_validator("case_id", "request", "routing_rationale", "validation_error", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return _strip_text(value)

    @model_validator(mode="after")
    def validate_result(self) -> "RoutingEvidenceResult":
        _reject_duplicates(self.expected_specialists, "expected_specialists")
        _reject_duplicates(self.actual_specialists, "actual_specialists")
        if self.structurally_valid:
            if self.validation_error:
                raise ValueError("valid structural results must not include validation_error.")
            if self.actual_status is None:
                raise ValueError("valid structural results must include actual_status.")
        else:
            if not self.validation_error:
                raise ValueError("invalid structural results must include validation_error.")
            if self.routing_correct:
                raise ValueError(
                    "routing_correct cannot be true when structural validation failed."
                )
        return self


class ManagerRunResult(AgentOrchestraModel):
    request: EditRequest
    plan: ManagerRoutingPlan
    latency_ms: StrictFloat = Field(ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    model: StrictStr = Field(min_length=1, max_length=160)

    @field_validator("model", mode="before")
    @classmethod
    def strip_model(cls, value: object) -> object:
        return _strip_text(value)


class RoutingBenchmarkReport(AgentOrchestraModel):
    generated_at: StrictStr = Field(min_length=1, max_length=40)
    model: StrictStr = Field(min_length=1, max_length=160)
    total_cases: StrictInt = Field(ge=0)
    structurally_valid_cases: StrictInt = Field(ge=0)
    correct_cases: StrictInt = Field(ge=0)
    structural_validity_rate: StrictStr
    routing_accuracy: StrictStr
    results: list[RoutingEvidenceResult] = Field(default_factory=list)

    @field_validator(
        "generated_at", "model", "structural_validity_rate", "routing_accuracy", mode="before"
    )
    @classmethod
    def strip_report_text(cls, value: object) -> object:
        return _strip_text(value)

    @model_validator(mode="after")
    def validate_summary_counts(self) -> "RoutingBenchmarkReport":
        if self.total_cases != len(self.results):
            raise ValueError("total_cases must equal the number of results.")
        if self.structurally_valid_cases > self.total_cases:
            raise ValueError("structurally_valid_cases cannot exceed total_cases.")
        if self.correct_cases > self.total_cases:
            raise ValueError("correct_cases cannot exceed total_cases.")
        return self


class CriterionResult(AgentOrchestraModel):
    criterion: StrictStr = Field(min_length=1, max_length=MAX_CRITERION_LENGTH)
    status: CriterionStatus
    evidence: StrictStr = Field(min_length=1, max_length=MAX_EVIDENCE_LENGTH)

    @field_validator("criterion", "evidence", mode="before")
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        return _strip_text(value)


class QAResult(AgentOrchestraModel):
    verdict: QAVerdict
    criteria_results: list[CriterionResult] = Field(min_length=1)
    reason: StrictStr = Field(min_length=1, max_length=MAX_REASON_LENGTH)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: object) -> object:
        return _strip_text(value)

    @model_validator(mode="after")
    def validate_verdict(self) -> "QAResult":
        criteria = [result.criterion for result in self.criteria_results]
        _reject_case_insensitive_duplicates(criteria, "criteria_results")
        statuses = [result.status for result in self.criteria_results]
        if self.verdict is QAVerdict.ACCEPT and any(
            status is CriterionStatus.FAILED for status in statuses
        ):
            raise ValueError("accept verdict requires every criterion to pass.")
        if self.verdict is QAVerdict.REJECT and not any(
            status is CriterionStatus.FAILED for status in statuses
        ):
            raise ValueError("reject verdict requires at least one failed criterion.")
        return self


def evaluate_routing_match(case: RoutingEvidenceCase, plan: ManagerRoutingPlan) -> bool:
    """Return whether a Manager plan matches a routing evidence case."""
    return case.expected_status == plan.status and set(case.expected_specialists) == set(
        plan.selected_specialists
    )


def _expected_execute_request_type(
    specialists: Sequence[SpecialistName],
    *,
    requested: str,
) -> str:
    selected = set(specialists)
    if SpecialistName.SEO in selected:
        return (
            SEO_DIAGNOSTIC_REQUEST_TYPE
            if requested == SEO_DIAGNOSTIC_REQUEST_TYPE
            else SEO_EDIT_REQUEST_TYPE
        )
    if selected == {SpecialistName.HTML}:
        return HTML_EDIT_REQUEST_TYPE
    if selected == {SpecialistName.CSS}:
        return CSS_EDIT_REQUEST_TYPE
    if selected == {SpecialistName.HTML, SpecialistName.CSS}:
        return HTML_CSS_EDIT_REQUEST_TYPE
    raise ValueError("execute plans contain an unsupported specialist combination.")


def validate_qa_coverage(
    acceptance_criteria: Sequence[str],
    qa_result: QAResult,
) -> None:
    """Verify QA result criteria exactly cover the Manager acceptance criteria."""
    expected = [_normalize_criterion(criterion) for criterion in acceptance_criteria]
    actual = [_normalize_criterion(result.criterion) for result in qa_result.criteria_results]

    if any(not criterion for criterion in expected):
        raise QACoverageError("Manager acceptance criteria must not contain blank values.")
    try:
        _reject_case_insensitive_duplicates(list(acceptance_criteria), "acceptance_criteria")
    except ValueError as exc:
        raise QACoverageError("Manager acceptance criteria must be unique.") from exc

    expected_set = set(expected)
    actual_set = set(actual)
    missing = expected_set - actual_set
    extra = actual_set - expected_set
    if missing:
        raise QACoverageError("QA result is missing Manager acceptance criteria.")
    if extra:
        raise QACoverageError("QA result includes criteria not defined by the Manager.")


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _strip_text_items(value: object) -> object:
    if isinstance(value, list):
        return [_strip_text(item) for item in value]
    return value


def _reject_blank_items(items: Sequence[str], field_name: str) -> None:
    if any(not item for item in items):
        raise ValueError(f"{field_name} must not contain blank values.")


def _reject_duplicates(items: Sequence[Any], field_name: str) -> None:
    if len(set(items)) != len(items):
        raise ValueError(f"{field_name} must contain unique values.")


def _reject_case_insensitive_duplicates(items: Sequence[str], field_name: str) -> None:
    normalized = [_normalize_criterion(item) for item in items]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must contain unique values.")


def _reject_overlong_items(items: Sequence[str], max_length: int, field_name: str) -> None:
    if any(len(item) > max_length for item in items):
        raise ValueError(f"{field_name} items must be at most {max_length} characters.")


def _normalize_criterion(value: str) -> str:
    return value.strip().casefold()


def _assignments_in_specialist_order(
    specialists: Sequence[SpecialistName],
    assignments: Sequence[SpecialistAssignment],
) -> list[SpecialistAssignment]:
    by_agent = {assignment.agent: assignment for assignment in assignments}
    return [by_agent[specialist] for specialist in specialists]


def _validate_simple_site_file(value: str, allowed_extensions: set[str]) -> None:
    if "\x00" in value:
        raise ValueError("file path must not contain null bytes.")
    if not value:
        raise ValueError("file path must not be blank.")
    if "/" in value or "\\" in value:
        raise ValueError("file path must be a simple filename, not a nested path.")
    path = PurePath(value)
    if path.is_absolute() or path.name != value:
        raise ValueError("file path must be a simple relative filename.")
    if value in {".", ".."} or ".." in value.split("."):
        raise ValueError("file path must not contain path traversal.")
    if path.suffix not in allowed_extensions:
        extensions = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"file path must use one of these extensions: {extensions}.")
