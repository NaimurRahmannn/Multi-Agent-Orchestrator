from __future__ import annotations

from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, frozen=True)


class WorkspaceHandle(WorkspaceModel):
    """Server-created handle for one staged editing run."""

    run_id: StrictStr = Field(min_length=1, max_length=80)
    path: Path
    staging_root: Path = Field(exclude=True)
    source_working_path: Path | None = None

    @field_validator("path", "staging_root", "source_working_path", mode="before")
    @classmethod
    def resolve_paths(cls, value: object) -> object:
        if value is None:
            return None
        return Path(value).expanduser().resolve()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if (
            "/" in value
            or "\\" in value
            or "\x00" in value
            or ".." in value
            or value.startswith(".")
        ):
            raise ValueError("run_id must be a safe generated identifier.")
        if not all(character.isalnum() or character in {"-", "_"} for character in value):
            raise ValueError("run_id may contain only letters, numbers, hyphens, and underscores.")
        return value

    @model_validator(mode="after")
    def validate_path_boundaries(self) -> WorkspaceHandle:
        if not self.path.is_absolute():
            raise ValueError("workspace path must be absolute.")
        if not self.staging_root.is_absolute():
            raise ValueError("staging_root must be absolute.")
        try:
            self.path.relative_to(self.staging_root)
        except ValueError as exc:
            raise ValueError("workspace path must be inside staging_root.") from exc
        if self.path == self.staging_root:
            raise ValueError("workspace path must be a child of staging_root.")
        if self.path.name != self.run_id:
            raise ValueError("workspace path final component must match run_id.")
        return self


class FileReadResult(WorkspaceModel):
    file: StrictStr = Field(min_length=1, max_length=120)
    start_line: StrictInt = Field(ge=1)
    end_line: StrictInt = Field(ge=1)
    total_lines: StrictInt = Field(ge=0)
    content: StrictStr
    truncated: StrictBool = False

    @model_validator(mode="after")
    def validate_line_range(self) -> FileReadResult:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")
        if self.end_line > self.total_lines and self.total_lines:
            raise ValueError("end_line must describe the actual returned range.")
        return self


class PatchExecutionResult(WorkspaceModel):
    file: StrictStr = Field(min_length=1, max_length=120)
    specialist: StrictStr = Field(min_length=1, max_length=20)
    summary: StrictStr = Field(min_length=1, max_length=300)
    replacements: StrictInt = Field(ge=1)


class DiffReport(WorkspaceModel):
    run_id: StrictStr = Field(min_length=1, max_length=80)
    changed_files: list[StrictStr] = Field(default_factory=list)
    unified_diff: StrictStr
