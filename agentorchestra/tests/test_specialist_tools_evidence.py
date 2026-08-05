import json

import pytest

from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import FileToolError, create_staged_copy
from agentorchestra.tools import PatchEvidenceRecorder, ProposePatchTool, ReadFileTool
from agentorchestra.workspace_models import PatchRejectionReason
from tests.specialist_helpers import applied_patch, rejected_patch
from tests.test_workspace_service import make_settings


def test_recorder_preserves_applied_rejected_order_and_snapshot_is_immutable():
    recorder = PatchEvidenceRecorder()
    first = rejected_patch()
    second = applied_patch()

    recorder.record(first)
    recorder.record(second)
    snapshot = recorder.snapshot()

    assert snapshot == (first, second)
    assert isinstance(snapshot, tuple)
    assert "content" not in snapshot[1].model_dump()


def test_recorders_are_isolated():
    first = PatchEvidenceRecorder()
    second = PatchEvidenceRecorder()
    first.record(applied_patch())

    assert len(first.snapshot()) == 1
    assert second.snapshot() == ()


def test_patch_tool_records_actual_applied_and_rejected_results(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "evidence")
    recorder = PatchEvidenceRecorder()
    tool = ProposePatchTool(
        handle=handle,
        specialist=SpecialistName.CSS,
        allowed_files=("style.css",),
        recorder=recorder,
    )

    tool._run(file="style.css", old_text="missing", new_text="new", summary="Reject.")
    tool._run(
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Apply.",
    )

    assert [result.status.value for result in recorder.snapshot()] == ["rejected", "applied"]


def test_existing_patch_tool_behavior_works_without_recorder(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "no-recorder")
    payload = json.loads(
        ProposePatchTool(handle=handle, specialist=SpecialistName.CSS)._run(
            file="style.css",
            old_text="  color: #ffffff;\n",
            new_text="  color: #f8faf7;\n",
            summary="Update text color.",
        )
    )

    assert payload["status"] == "applied"


def test_html_bound_tools_read_and_patch_only_target_page(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "html-scope")
    read_tool = ReadFileTool(handle=handle, allowed_files=("index.html",))
    patch_tool = ProposePatchTool(
        handle=handle,
        specialist=SpecialistName.HTML,
        allowed_files=("index.html",),
    )

    assert json.loads(read_tool._run(file="index.html"))["file"] == "index.html"
    with pytest.raises(FileToolError, match="approved read scope"):
        read_tool._run(file="about.html")
    assert json.loads(
        patch_tool._run(
            file="index.html",
            old_text="  <h1>Home</h1>\n",
            new_text="  <h1>Updated home</h1>\n",
            summary="Update heading.",
        )
    )["status"] == "applied"
    for file in ("about.html", "style.css"):
        payload = json.loads(
            patch_tool._run(file=file, old_text="old", new_text="new", summary="Reject.")
        )
        assert payload["rejection_reason"] == PatchRejectionReason.UNAUTHORIZED_FILE.value


def test_css_bound_tools_read_page_and_css_but_patch_only_css(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-scope")
    read_tool = ReadFileTool(handle=handle, allowed_files=("index.html", "style.css"))
    patch_tool = ProposePatchTool(
        handle=handle,
        specialist=SpecialistName.CSS,
        allowed_files=("style.css",),
    )

    assert json.loads(read_tool._run(file="index.html"))["file"] == "index.html"
    assert json.loads(read_tool._run(file="style.css"))["file"] == "style.css"
    with pytest.raises(FileToolError):
        read_tool._run(file="about.html")
    html_rejection = json.loads(
        patch_tool._run(file="index.html", old_text="old", new_text="new", summary="Reject.")
    )
    asset_rejection = json.loads(
        patch_tool._run(file="asset.svg", old_text="old", new_text="new", summary="Reject.")
    )
    assert html_rejection["rejection_reason"] == "unauthorized_file"
    assert asset_rejection["rejection_reason"] == "unauthorized_file"


def test_model_schema_cannot_override_trusted_context(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "schema-scope")
    recorder = PatchEvidenceRecorder()
    read_tool = ReadFileTool(handle=handle, allowed_files=("index.html",))
    patch_tool = ProposePatchTool(
        handle=handle,
        specialist=SpecialistName.HTML,
        allowed_files=("index.html",),
        recorder=recorder,
    )

    assert set(read_tool.args_schema.model_json_schema()["properties"]) == {
        "file",
        "start_line",
        "end_line",
    }
    assert set(patch_tool.args_schema.model_json_schema()["properties"]) == {
        "file",
        "old_text",
        "new_text",
        "summary",
    }
    serialized = patch_tool.model_dump(mode="json")
    for hidden in ("handle", "workspace", "specialist", "allowed_files", "recorder"):
        assert hidden not in serialized


def test_trusted_file_scope_is_validated_deduplicated_and_sorted(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "normalized-scope")
    tool = ReadFileTool(
        handle=handle,
        allowed_files=("style.css", "index.html", "style.css"),
    )

    assert tool.allowed_files == ("index.html", "style.css")
    with pytest.raises(FileToolError):
        ReadFileTool(handle=handle, allowed_files=("../index.html",))
