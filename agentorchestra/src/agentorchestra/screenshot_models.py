from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from agentorchestra.models import AgentOrchestraModel, EditRequest
from agentorchestra.path_safety import reject_absolute_path_text, validate_relative_site_path


class ScreenshotKind(StrEnum):
    BEFORE = "before"
    PROPOSED_AFTER = "proposed_after"


class ScreenshotStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ScreenshotArtifact(AgentOrchestraModel):
    kind: ScreenshotKind
    status: ScreenshotStatus
    run_id: StrictStr = Field(min_length=1, max_length=80)
    target_page: StrictStr = Field(min_length=1, max_length=120)
    source_site_digest: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    relative_path: StrictStr | None = Field(default=None, max_length=240)
    viewport_width: StrictInt = Field(default=1440, gt=0)
    viewport_height: StrictInt = Field(default=900, gt=0)
    full_page: StrictBool = True
    latency_ms: StrictFloat = Field(ge=0)
    error: StrictStr | None = Field(default=None, min_length=1, max_length=1_000)
    warnings: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_artifact(self) -> ScreenshotArtifact:
        _validate_run_id(self.run_id)
        EditRequest(target_page=self.target_page, instruction="Validate screenshot target.")
        if not self.full_page:
            raise ValueError("Screenshot artifacts must represent full-page capture.")
        if self.relative_path is not None:
            path = validate_relative_site_path(self.relative_path)
            if not path.startswith(f"reports/screenshots/{self.run_id}/") or not path.endswith(
                ".png"
            ):
                raise ValueError("Screenshot path must be inside its generated run directory.")
        if self.status is ScreenshotStatus.SUCCEEDED:
            if self.relative_path is None or self.source_site_digest is None or self.error is not None:
                raise ValueError("Successful screenshot evidence is incomplete.")
        elif self.status is ScreenshotStatus.FAILED:
            if self.error is None or self.relative_path is not None:
                raise ValueError("Failed screenshot evidence requires only a safe error.")
        elif self.relative_path is not None or self.error is not None:
            raise ValueError("Skipped screenshots cannot contain a path or error.")
        for value in [self.error, *self.warnings]:
            if value is not None:
                reject_absolute_path_text(
                    value,
                    message="Screenshot evidence must not expose absolute paths.",
                )
                if "gsk_" in value.lower():
                    raise ValueError("Screenshot evidence must not expose API keys.")
        return self


def _validate_run_id(value: str) -> str:
    if (
        not value
        or value.startswith(".")
        or ".." in value
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in value)
    ):
        raise ValueError("run_id must be a safe generated identifier.")
    return value
