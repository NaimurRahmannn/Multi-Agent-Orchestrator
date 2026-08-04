import json

from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.tools import ProposePatchTool, ReadFileTool
from agentorchestra.workspace_models import PatchRejectionReason, PatchStatus
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


def test_bound_patch_tool_returns_structured_ownership_rejection(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "tool-owner")
    css_tool = ProposePatchTool(handle=handle, specialist=SpecialistName.CSS)

    payload = json.loads(
        css_tool._run(
            file="index.html",
            old_text="<h1>Home</h1>",
            new_text="<h1>Updated</h1>",
            summary="Try HTML edit.",
        )
    )

    assert payload["status"] == PatchStatus.REJECTED.value
    assert payload["rejection_reason"] == PatchRejectionReason.UNAUTHORIZED_FILE.value
    assert payload["replacements"] == 0


def test_patch_tool_returns_deterministic_json_for_missing_and_ambiguous_targets(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "tool-rejections")
    tool = ProposePatchTool(handle=handle, specialist=SpecialistName.CSS)

    missing_json = tool._run(
        file="style.css",
        old_text="not present",
        new_text="replacement",
        summary="Missing target.",
    )
    repeated_json = tool._run(
        file="style.css",
        old_text="}\n",
        new_text="}\n/* changed */\n",
        summary="Repeated target.",
    )

    assert missing_json == tool._run(
        file="style.css",
        old_text="not present",
        new_text="replacement",
        summary="Missing target.",
    )
    assert json.loads(missing_json)["rejection_reason"] == "target_not_found"
    assert json.loads(missing_json)["match_count"] == 0
    assert json.loads(repeated_json)["rejection_reason"] == "ambiguous_target"
    assert json.loads(repeated_json)["match_count"] == 2


def test_actual_crewai_input_schemas_hide_trusted_server_fields(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "tool-schema")
    read_tool = ReadFileTool(handle=handle)
    patch_tool = ProposePatchTool(handle=handle, specialist=SpecialistName.CSS)

    read_properties = set(read_tool.args_schema.model_json_schema()["properties"])
    patch_properties = set(patch_tool.args_schema.model_json_schema()["properties"])

    assert read_properties == {"file", "start_line", "end_line"}
    assert patch_properties == {"file", "old_text", "new_text", "summary"}
    for hidden in {"handle", "workspace", "run_id", "root", "settings", "specialist"}:
        assert hidden not in read_properties
        assert hidden not in patch_properties
