from __future__ import annotations

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator

from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import (
    normalize_allowed_files,
    propose_patch,
    read_file,
    update_css_declaration,
)
from agentorchestra.tools.evidence import PatchEvidenceRecorder
from agentorchestra.workspace_models import WorkspaceHandle


class ReadFileInput(BaseModel):
    file: str = Field(description="Simple staged-site filename, such as index.html or style.css.")
    start_line: int = Field(default=1, ge=1, description="One-based first line to read.")
    end_line: int | None = Field(default=None, ge=1, description="One-based final line to read.")


class ProposePatchInput(BaseModel):
    file: str = Field(description="Simple staged-site filename, such as index.html or style.css.")
    old_text: str = Field(
        min_length=1,
        description=(
            "Verbatim unique substring copied from the content field of the most recent "
            "read_file result for this file; preserve whitespace and newlines exactly. "
            "Do not use an empty string. For insertions, anchor on a real exact substring "
            "such as a closing tag and include the inserted text in new_text."
        ),
    )
    new_text: str = Field(
        min_length=1,
        description=(
            "Minimal replacement made by copying old_text and changing only the required "
            "characters; preserve all unaffected text exactly."
        ),
    )
    summary: str = Field(min_length=1, description="Concise summary of the proposed edit.")


class UpdateCSSDeclarationInput(BaseModel):
    selector: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Exact existing simple selector discovered from index/about/contact HTML and "
            "style.css, such as .button-link. Do not include braces or declarations."
        ),
    )
    property_name: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^(?:--)?[A-Za-z][A-Za-z0-9-]*$",
        description="Existing CSS property to update, such as background or color.",
    )
    value: str = Field(
        min_length=1,
        max_length=500,
        description="New declaration value only, such as #dc2626; omit the trailing semicolon.",
    )
    summary: str = Field(min_length=1, max_length=300, description="Concise edit summary.")

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        stripped = value.strip()
        if any(character in stripped for character in "{};\r\n") or "/*" in stripped:
            raise ValueError("selector must name one simple existing rule.")
        return stripped

    @field_validator("property_name")
    @classmethod
    def normalize_property_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        stripped = value.strip()
        if (
            any(character in stripped for character in ";{}\r\n")
            or "/*" in stripped
            or "*/" in stripped
        ):
            raise ValueError("value must contain one declaration value without a semicolon.")
        return stripped


class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = (
        "Read a bounded line range from one approved staged file. Before patching, copy "
        "old_text verbatim from the returned content field."
    )
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
    description: str = (
        "Apply one exact unique replacement. old_text must be copied verbatim from the most "
        "recent read_file content for the same staged file."
    )
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


class UpdateCSSDeclarationTool(BaseTool):
    name: str = "update_css_declaration"
    description: str = (
        "Safely update one existing property in one uniquely matched simple style.css rule. "
        "Prefer this for ordinary color, typography, spacing, border, and layout value changes. "
        "It preserves the rule and applies the same staged atomic patch safeguards."
    )
    args_schema: type[BaseModel] = UpdateCSSDeclarationInput
    handle: WorkspaceHandle = Field(exclude=True)
    specialist: SpecialistName = Field(default=SpecialistName.CSS, exclude=True)
    allowed_files: tuple[str, ...] | None = Field(default=None, exclude=True)
    recorder: PatchEvidenceRecorder | None = Field(default=None, exclude=True)

    @field_validator("allowed_files", mode="before")
    @classmethod
    def normalize_scope(cls, value: object) -> object:
        return normalize_allowed_files(value)

    def _run(self, selector: str, property_name: str, value: str, summary: str) -> str:
        result = update_css_declaration(
            self.handle,
            specialist=self.specialist,
            selector=selector,
            property_name=property_name,
            value=value,
            summary=summary,
            allowed_files=self.allowed_files,
        )
        if self.recorder is not None:
            self.recorder.record(result)
        return result.model_dump_json()
