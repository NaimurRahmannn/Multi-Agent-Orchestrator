import json

import pytest

from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import FileToolError, create_staged_copy
from agentorchestra.tools import ProposePatchTool, ReadFileTool
from tests.test_workspace_service import make_settings


def test_read_file_tool_is_bound_to_workspace_and_returns_json(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "tool-read")
    tool = ReadFileTool(handle=handle)

    payload = json.loads(tool._run(file="style.css", start_line=5, end_line=5))

    assert payload["file"] == "style.css"
    assert payload["content"] == ".button-link {\n"
    assert "handle" not in tool.model_dump(mode="json")


def test_propose_patch_tool_uses_bound_specialist_identity(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "tool-patch")
    css_tool = ProposePatchTool(handle=handle, specialist=SpecialistName.CSS)

    payload = json.loads(
        css_tool._run(
            file="style.css",
            old_text="  color: #ffffff;\n",
            new_text="  color: #f8faf7;\n",
            summary="Soften button text.",
        )
    )

    assert payload["specialist"] == "css"
    assert "specialist" not in css_tool.model_dump(mode="json")
    assert "color: #f8faf7;" in (handle.path / "style.css").read_text(encoding="utf-8")


def test_bound_patch_tool_rejects_file_outside_specialist_ownership(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "tool-owner")
    css_tool = ProposePatchTool(handle=handle, specialist=SpecialistName.CSS)

    with pytest.raises(FileToolError):
        css_tool._run(
            file="index.html",
            old_text="<h1>Home</h1>",
            new_text="<h1>Updated</h1>",
            summary="Try HTML edit.",
        )
