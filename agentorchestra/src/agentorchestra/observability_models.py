from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from agentorchestra.models import AgentOrchestraModel, SpecialistName, TokenUsage
from agentorchestra.path_safety import reject_absolute_path_text


class TimelineStage(StrEnum):
    MANAGER = "manager"
    WORKSPACE = "workspace"
    SCREENSHOT_BEFORE = "screenshot_before"
    SPECIALIST_HTML = "specialist_html"
    SPECIALIST_CSS = "specialist_css"
    SPECIALIST_SEO = "specialist_seo"
    LIGHTHOUSE = "lighthouse"
    EVIDENCE_VALIDATION = "evidence_validation"
    SCREENSHOT_PROPOSED_AFTER = "screenshot_proposed_after"
    QA = "qa"
    PROMOTION = "promotion"
    CLEANUP = "cleanup"
    DIAGNOSTIC_FINALIZE = "diagnostic_finalize"


class TimelineEventStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    WARNING = "warning"


_SPECIALIST_STAGE = {
    TimelineStage.SPECIALIST_HTML: SpecialistName.HTML,
    TimelineStage.SPECIALIST_CSS: SpecialistName.CSS,
    TimelineStage.SPECIALIST_SEO: SpecialistName.SEO,
}


class TimelineEvent(AgentOrchestraModel):
    sequence: StrictInt = Field(ge=0)
    stage: TimelineStage
    status: TimelineEventStatus
    specialist: SpecialistName | None = None
    started_offset_ms: StrictFloat = Field(ge=0)
    duration_ms: StrictFloat = Field(ge=0)
    message: StrictStr = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_event(self) -> TimelineEvent:
        expected = _SPECIALIST_STAGE.get(self.stage)
        if expected is None and self.specialist is not None:
            raise ValueError("Only specialist stages may identify a specialist.")
        if expected is not None and self.specialist is not expected:
            raise ValueError("Specialist stage and specialist must match.")
        reject_absolute_path_text(
            self.message,
            message="Timeline messages must not expose absolute paths.",
        )
        if "gsk_" in self.message.lower():
            raise ValueError("Timeline messages must not expose API keys.")
        return self


class RunTimeline(AgentOrchestraModel):
    run_id: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    events: list[TimelineEvent] = Field(default_factory=list)
    total_observed_duration_ms: StrictFloat = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> RunTimeline:
        sequences = [event.sequence for event in self.events]
        offsets = [event.started_offset_ms for event in self.events]
        if any(
            current <= previous
            for previous, current in zip(sequences, sequences[1:], strict=False)
        ):
            raise ValueError("Timeline sequences must strictly increase.")
        if any(
            current < previous
            for previous, current in zip(offsets, offsets[1:], strict=False)
        ):
            raise ValueError("Timeline offsets must not move backward.")
        if self.events and self.total_observed_duration_ms < max(
            event.started_offset_ms + event.duration_ms for event in self.events
        ):
            raise ValueError("Timeline duration must cover every event.")
        return self


class ObservedAgentRole(StrEnum):
    MANAGER = "manager"
    HTML = "html"
    CSS = "css"
    SEO = "seo"
    QA = "qa"


class AgentTokenUsage(AgentOrchestraModel):
    role: ObservedAgentRole
    usage: TokenUsage


class RunMetrics(AgentOrchestraModel):
    total_latency_ms: StrictFloat = Field(ge=0)
    manager_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    specialist_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    html_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    css_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    seo_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    lighthouse_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    qa_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    screenshot_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    promotion_latency_ms: StrictFloat = Field(default=0.0, ge=0)
    applied_patch_count: StrictInt = Field(default=0, ge=0)
    rejected_patch_count: StrictInt = Field(default=0, ge=0)
    changed_file_count: StrictInt = Field(default=0, ge=0)
    added_lines: StrictInt = Field(default=0, ge=0)
    removed_lines: StrictInt = Field(default=0, ge=0)
    lighthouse_score: StrictInt | None = Field(default=None, ge=0, le=100)
    token_usage_by_role: list[AgentTokenUsage] = Field(default_factory=list)
    known_prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    known_completion_tokens: StrictInt | None = Field(default=None, ge=0)
    known_total_tokens: StrictInt | None = Field(default=None, ge=0)
    token_usage_complete: StrictBool = False

    @model_validator(mode="after")
    def validate_roles(self) -> RunMetrics:
        roles = [item.role for item in self.token_usage_by_role]
        if len(roles) != len(set(roles)):
            raise ValueError("Token usage roles must be unique.")
        return self
