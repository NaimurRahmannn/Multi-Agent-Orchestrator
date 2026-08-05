from __future__ import annotations

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator

from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import normalize_allowed_files, propose_patch, read_file
from agentorchestra.tools.evidence import PatchEvidenceRecorder
from agentorchestra.workspace_models import WorkspaceHandle


class ReadFileInput(BaseModel):
    file: str = Field(description="Simple staged-site filename, such as index.html or style.css.")
    start_line: int = Field(default=1, ge=1, description="One-based first line to read.")
    end_line: int | None = Field(default=None, ge=1, description="One-based final line to read.")


class ProposePatchInput(BaseModel):
    file: str = Field(description="Simple staged-site filename, such as index.html or style.css.")
    old_text: str = Field(min_length=1, description="Exact existing text to replace.")
    new_text: str = Field(min_length=1, description="Exact replacement text.")
    summary: str = Field(min_length=1, description="Concise summary of the proposed edit.")


class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = "Read a bounded line range from one approved file in the staged site."
    args_schema: type[BaseModel] = ReadFileInput
    handle: WorkspaceHandle = Field(exclude=True)
    allowed_files: tuple[str, ...] | None = Field(default=None, exclude=True)

    @field_validator("allowed_files", mode="before")
    @classmethod
    def normalize_scope(cls, value: object) -> object:
        return normalize_allowed_files(value)

    def _run(self, file: str, start_line: int = 1, end_line: int | None = None) -> str:
        result = read_file(
            self.handle,
            file=file,
            start_line=start_line,
            end_line=end_line,
            allowed_files=self.allowed_files,
        )
        return result.model_dump_json()


class ProposePatchTool(BaseTool):
    name: str = "propose_patch"
    description: str = "Apply one exact unique text replacement to an approved staged-site file."
    args_schema: type[BaseModel] = ProposePatchInput
    handle: WorkspaceHandle = Field(exclude=True)
    specialist: SpecialistName = Field(exclude=True)
    allowed_files: tuple[str, ...] | None = Field(default=None, exclude=True)
    recorder: PatchEvidenceRecorder | None = Field(default=None, exclude=True)

    @field_validator("allowed_files", mode="before")
    @classmethod
    def normalize_scope(cls, value: object) -> object:
        return normalize_allowed_files(value)

    def _run(self, file: str, old_text: str, new_text: str, summary: str) -> str:
        result = propose_patch(
            self.handle,
            specialist=self.specialist,
            file=file,
            old_text=old_text,
            new_text=new_text,
            summary=summary,
            allowed_files=self.allowed_files,
        )
        if self.recorder is not None:
            self.recorder.record(result)
        return result.model_dump_json()
