from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


class RoutingStatus(StrEnum):
    EXECUTE = "execute"
    CLARIFICATION_REQUIRED = "clarification_required"
    OUT_OF_SCOPE = "out_of_scope"


class SpecialistName(StrEnum):
    HTML = "html"
    CSS = "css"
    SEO = "seo"


class SpecialistAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: SpecialistName
    task: StrictStr = Field(min_length=1)

    @field_validator("task", mode="before")
    @classmethod
    def strip_task(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ManagerRoutingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RoutingStatus
    request_type: StrictStr = Field(min_length=1)
    selected_specialists: list[SpecialistName] = Field(default_factory=list)
    routing_rationale: StrictStr | None = None
    assignments: list[SpecialistAssignment] = Field(default_factory=list)
    acceptance_criteria: list[StrictStr] = Field(default_factory=list)
    clarification_question: StrictStr | None = None
    rejection_reason: StrictStr | None = None

    @field_validator(
        "request_type",
        "routing_rationale",
        "clarification_question",
        "rejection_reason",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def strip_criteria(cls, value: object) -> object:
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value

    @model_validator(mode="after")
    def validate_status_invariants(self) -> "ManagerRoutingPlan":
        if len(set(self.selected_specialists)) != len(self.selected_specialists):
            raise ValueError("selected_specialists must be unique.")

        assignment_agents = [assignment.agent for assignment in self.assignments]
        if len(set(assignment_agents)) != len(assignment_agents):
            raise ValueError("assignments must be unique by agent.")

        blank_criteria = [item for item in self.acceptance_criteria if not item]
        if blank_criteria:
            raise ValueError("acceptance_criteria must not contain blank items.")

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
        if not self.acceptance_criteria:
            raise ValueError("execute plans must include acceptance criteria.")
        if not self.routing_rationale:
            raise ValueError("execute plans must include a routing rationale.")
        if self.clarification_question:
            raise ValueError("execute plans must not include a clarification question.")
        if self.rejection_reason:
            raise ValueError("execute plans must not include a rejection reason.")

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
