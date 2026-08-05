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


def test_exact_grouped_css_anchor_supports_narrow_single_selector_override(tmp_path):
    settings = make_settings(tmp_path)
    grouped_css = (
        ".hero-copy h1,\n"
        ".page-intro h1 {\n"
        "  margin: 0 0 16px;\n"
        "  font-size: 3rem;\n"
        "  line-height: 1.1;\n"
        "}\n"
    )
    for site_dir in (settings.fixture_site_dir, settings.working_site_dir):
        (site_dir / "style.css").write_text(grouped_css, encoding="utf-8")
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "grouped-selector")
    read_tool = ReadFileTool(handle=handle, allowed_files=("style.css",))
    patch_tool = ProposePatchTool(
        handle=handle,
        specialist=SpecialistName.CSS,
        allowed_files=("style.css",),
    )
    read_payload = json.loads(
        read_tool._run(file="style.css", start_line=1, end_line=6)
    )
    old_block = read_payload["content"]
    new_block = old_block + "\n.hero-copy h1 {\n  font-size: 3.5rem;\n}\n"

    result = json.loads(
        patch_tool._run(
            file="style.css",
            old_text=old_block,
            new_text=new_block,
            summary="Add a narrow hero heading override.",
        )
    )
    updated = (handle.path / "style.css").read_text(encoding="utf-8")

    assert result["status"] == "applied"
    assert ".hero-copy h1,\n.page-intro h1 {" in updated
    assert ".hero-copy h1 {\n  font-size: 3.5rem;\n}" in updated


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
    read_description = read_tool.description.casefold()
    patch_description = patch_tool.description.casefold()
    patch_properties = patch_tool.args_schema.model_json_schema()["properties"]
    assert "copy old_text verbatim" in read_description
    assert "most recent read_file content" in patch_description
    assert "verbatim unique substring" in patch_properties["old_text"]["description"].casefold()
    assert "preserve all unaffected text" in patch_properties["new_text"]["description"].casefold()
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
