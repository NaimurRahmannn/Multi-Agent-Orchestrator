from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Literal

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


class PatchStatus(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


class PatchRejectionReason(StrEnum):
    TARGET_NOT_FOUND = "target_not_found"
    AMBIGUOUS_TARGET = "ambiguous_target"
    UNAUTHORIZED_FILE = "unauthorized_file"
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    UNSAFE_PATH = "unsafe_path"
    FILE_NOT_FOUND = "file_not_found"
    INVALID_ENCODING = "invalid_encoding"
    FILE_TOO_LARGE = "file_too_large"
    PATCH_TOO_LARGE = "patch_too_large"
    NO_OP = "no_op"
    INVALID_PATCH = "invalid_patch"
    OWNERSHIP_VIOLATION = "ownership_violation"


class WorkspaceLimits(WorkspaceModel):
    """Immutable, server-controlled limits for staged text operations."""

    max_file_bytes: StrictInt = Field(default=256 * 1024, ge=1)
    max_read_lines: StrictInt = Field(default=120, ge=1)
    max_old_text_bytes: StrictInt = Field(default=5_000, ge=1)
    max_new_text_bytes: StrictInt = Field(default=5_000, ge=1)
    max_combined_diff_bytes: StrictInt = Field(default=512 * 1024, ge=1)


class WorkspaceHandle(WorkspaceModel):
    """Server-created handle for one staged editing run."""

    run_id: StrictStr = Field(min_length=1, max_length=80)
    path: Path
    staging_root: Path = Field(exclude=True)
    source_working_path: Path | None = None
    source_working_digest: StrictStr | None = Field(
        default=None,
        exclude=True,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("path", "staging_root", "source_working_path", mode="before")
    @classmethod
    def resolve_paths(cls, value: object) -> object:
        if value is None:
            return None
        # Keep symlink identity intact. Filesystem boundaries resolve only after
        # checking each candidate path for symlinks.
        return Path(os.path.abspath(Path(value).expanduser()))

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
    status: PatchStatus
    file: StrictStr = Field(min_length=1, max_length=120)
    specialist: StrictStr = Field(min_length=1, max_length=20)
    summary: StrictStr = Field(min_length=1, max_length=300)
    match_count: StrictInt | None = Field(default=None, ge=0)
    replacements: StrictInt = Field(default=0, ge=0, le=1)
    bytes_before: StrictInt | None = Field(default=None, ge=0)
    bytes_after: StrictInt | None = Field(default=None, ge=0)
    before_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    after_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rejection_reason: PatchRejectionReason | None = None
    message: StrictStr = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_result_contract(self) -> PatchExecutionResult:
        if self.status is PatchStatus.APPLIED:
            if self.match_count != 1 or self.replacements != 1:
                raise ValueError("applied patches must report exactly one match and replacement.")
            if None in {
                self.bytes_before,
                self.bytes_after,
                self.before_sha256,
                self.after_sha256,
            }:
                raise ValueError("applied patches must include byte counts and SHA-256 hashes.")
            if self.before_sha256 == self.after_sha256:
                raise ValueError("applied patch hashes must differ.")
            if self.rejection_reason is not None:
                raise ValueError("applied patches must not include a rejection reason.")
            return self

        if self.replacements != 0:
            raise ValueError("rejected patches must not report replacements.")
        if self.rejection_reason is None:
            raise ValueError("rejected patches must include a rejection reason.")
        if self.after_sha256 is not None and self.after_sha256 != self.before_sha256:
            raise ValueError("rejected patches must not report changed content hashes.")
        if self.bytes_after is not None and self.bytes_after != self.bytes_before:
            raise ValueError("rejected patches must not report changed byte counts.")
        return self


class FileDiff(WorkspaceModel):
    file: StrictStr = Field(min_length=1, max_length=120)
    change_type: Literal["modified"] = "modified"
    unified_diff: StrictStr
    added_lines: StrictInt = Field(ge=0)
    removed_lines: StrictInt = Field(ge=0)


class DiffReport(WorkspaceModel):
    run_id: StrictStr = Field(min_length=1, max_length=80)
    changed_files: list[StrictStr] = Field(default_factory=list)
    files: list[FileDiff] = Field(default_factory=list)
    combined_diff: StrictStr = ""
    is_empty: StrictBool = True
    total_added_lines: StrictInt = Field(default=0, ge=0)
    total_removed_lines: StrictInt = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_diff_contract(self) -> DiffReport:
        file_names = [file.file for file in self.files]
        if self.changed_files != file_names or self.changed_files != sorted(self.changed_files):
            raise ValueError("changed_files must match files in deterministic path order.")
        if self.is_empty != (not self.changed_files):
            raise ValueError("is_empty must reflect whether changed files are present.")
        if self.combined_diff != "".join(file.unified_diff for file in self.files):
            raise ValueError("combined_diff must equal the ordered per-file diffs.")
        if self.total_added_lines != sum(file.added_lines for file in self.files):
            raise ValueError("total_added_lines must equal the per-file total.")
        if self.total_removed_lines != sum(file.removed_lines for file in self.files):
            raise ValueError("total_removed_lines must equal the per-file total.")
        return self

    @property
    def unified_diff(self) -> str:
        """Backward-compatible alias for the combined deterministic diff."""
        return self.combined_diff
