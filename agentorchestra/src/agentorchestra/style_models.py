from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictStr, field_validator, model_validator

from agentorchestra.models import AgentOrchestraModel
from agentorchestra.workspace_models import PatchExecutionResult


class StylePlanStatus(StrEnum):
    EXECUTE = "execute"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"


class StyleOperation(StrEnum):
    SET_BACKGROUND_COLOR = "set_background_color"
    SET_TEXT_COLOR = "set_text_color"
    SET_BORDER_RADIUS = "set_border_radius"
    INCREASE_BORDER_RADIUS = "increase_border_radius"
    DECREASE_BORDER_RADIUS = "decrease_border_radius"
    SET_HEIGHT = "set_height"
    INCREASE_HEIGHT = "increase_height"
    DECREASE_HEIGHT = "decrease_height"
    SET_GAP = "set_gap"
    INCREASE_GAP = "increase_gap"
    DECREASE_GAP = "decrease_gap"
    SET_FONT_SIZE = "set_font_size"
    INCREASE_FONT_SIZE = "increase_font_size"
    DECREASE_FONT_SIZE = "decrease_font_size"
    SET_PADDING = "set_padding"
    INCREASE_PADDING = "increase_padding"
    DECREASE_PADDING = "decrease_padding"


class StyleAmount(StrEnum):
    SLIGHT = "slight"
    MODERATE = "moderate"
    LARGE = "large"


class StyleIntentPlan(AgentOrchestraModel):
    status: StylePlanStatus
    target_id: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    operation: StyleOperation | None = None
    value: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    amount: StyleAmount = StyleAmount.MODERATE
    summary: StrictStr = Field(min_length=1, max_length=300)
    clarification_question: StrictStr | None = Field(default=None, max_length=500)
    reason: StrictStr | None = Field(default=None, max_length=500)

    @field_validator(
        "target_id", "value", "summary", "clarification_question", "reason", mode="before"
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_plan(self) -> StyleIntentPlan:
        if self.status is StylePlanStatus.EXECUTE:
            if self.target_id is None or self.operation is None:
                raise ValueError("execute style plans require target_id and operation.")
            if self.clarification_question is not None or self.reason is not None:
                raise ValueError("execute style plans cannot include clarification or reason.")
            set_operations = {
                StyleOperation.SET_BACKGROUND_COLOR,
                StyleOperation.SET_TEXT_COLOR,
                StyleOperation.SET_BORDER_RADIUS,
                StyleOperation.SET_HEIGHT,
                StyleOperation.SET_GAP,
                StyleOperation.SET_FONT_SIZE,
                StyleOperation.SET_PADDING,
            }
            if self.operation in set_operations and self.value is None:
                raise ValueError("set operations require a requested value.")
            if self.operation not in set_operations and self.value is not None:
                raise ValueError("relative style operations cannot include a value.")
        elif self.status is StylePlanStatus.CLARIFICATION_REQUIRED:
            if not self.clarification_question:
                raise ValueError("clarification style plans require one question.")
            if self.target_id is not None or self.operation is not None or self.reason is not None:
                raise ValueError("clarification style plans cannot include execution fields.")
        else:
            if not self.reason:
                raise ValueError("unsupported style plans require a reason.")
            if self.target_id is not None or self.operation is not None:
                raise ValueError("unsupported style plans cannot include execution fields.")
            if self.clarification_question is not None:
                raise ValueError("unsupported style plans cannot include a question.")
        return self


class StyleComponent(AgentOrchestraModel):
    id: StrictStr = Field(min_length=1, max_length=120)
    page: StrictStr = Field(min_length=1, max_length=120)
    selector: StrictStr = Field(min_length=1, max_length=200)
    label: StrictStr = Field(min_length=1, max_length=160)
    aliases: list[StrictStr] = Field(min_length=1)
    operations: dict[StyleOperation, StrictStr] = Field(min_length=1)


class StyleChangeEvidence(AgentOrchestraModel):
    target_id: StrictStr = Field(min_length=1, max_length=120)
    label: StrictStr = Field(min_length=1, max_length=160)
    selector: StrictStr = Field(min_length=1, max_length=200)
    property_name: StrictStr = Field(min_length=1, max_length=80)
    before_value: StrictStr = Field(min_length=1, max_length=500)
    after_value: StrictStr = Field(min_length=1, max_length=500)
    expected_relation: StrictStr = Field(min_length=1, max_length=40)
    source_verified: bool = True
    computed_before_value: StrictStr | None = Field(default=None, max_length=500)
    computed_after_value: StrictStr | None = Field(default=None, max_length=500)
    computed_verified: bool | None = None


class StyleExecutionStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_SATISFIED = "already_satisfied"
    CLARIFICATION_REQUIRED = "clarification_required"
    BLOCKED = "blocked"


class StyleExecutionResult(AgentOrchestraModel):
    status: StyleExecutionStatus
    plan: StyleIntentPlan
    summary: StrictStr = Field(min_length=1, max_length=300)
    patch: PatchExecutionResult | None = None
    evidence: StyleChangeEvidence | None = None
    clarification_question: StrictStr | None = Field(default=None, max_length=500)
    remaining_issue: StrictStr | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_result(self) -> StyleExecutionResult:
        if self.status is StyleExecutionStatus.APPLIED:
            if self.patch is None or self.patch.status.value != "applied" or self.evidence is None:
                raise ValueError("applied style results require patch and style evidence.")
        elif self.status is StyleExecutionStatus.ALREADY_SATISFIED:
            if self.patch is not None or self.evidence is None:
                raise ValueError("already-satisfied style results require evidence without patch.")
        elif self.status is StyleExecutionStatus.CLARIFICATION_REQUIRED:
            if not self.clarification_question or self.patch is not None:
                raise ValueError("clarification style results require a question without patch.")
        elif not self.remaining_issue or self.patch is not None:
            raise ValueError("blocked style results require an issue without patch.")
        return self
