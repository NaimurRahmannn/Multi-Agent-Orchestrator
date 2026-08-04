import pytest
from pydantic import ValidationError

from agentorchestra.workspace_models import FileReadResult, WorkspaceHandle


def handle_payload(tmp_path, **overrides):
    staging_root = tmp_path / "sites" / "staging"
    payload = {
        "run_id": "abc123",
        "path": staging_root / "abc123",
        "staging_root": staging_root,
        "source_working_path": tmp_path / "sites" / "working",
    }
    payload.update(overrides)
    return payload


def test_workspace_handle_accepts_safe_child_path(tmp_path):
    handle = WorkspaceHandle(**handle_payload(tmp_path))

    assert handle.run_id == "abc123"
    assert handle.path == (tmp_path / "sites" / "staging" / "abc123").resolve()
    assert handle.model_dump(mode="json")["path"] == str(handle.path)


@pytest.mark.parametrize("run_id", ["../bad", "bad/name", "bad\\name", ".hidden", "bad..id", "bad id"])
def test_workspace_handle_rejects_unsafe_run_ids(tmp_path, run_id):
    with pytest.raises(ValidationError):
        WorkspaceHandle(**handle_payload(tmp_path, run_id=run_id, path=tmp_path / "sites" / "staging" / run_id))


def test_workspace_handle_rejects_path_outside_staging_root(tmp_path):
    with pytest.raises(ValidationError):
        WorkspaceHandle(**handle_payload(tmp_path, path=tmp_path / "elsewhere" / "abc123"))


def test_workspace_handle_requires_path_name_to_match_run_id(tmp_path):
    with pytest.raises(ValidationError):
        WorkspaceHandle(**handle_payload(tmp_path, path=tmp_path / "sites" / "staging" / "other"))


def test_workspace_handle_is_effectively_immutable(tmp_path):
    handle = WorkspaceHandle(**handle_payload(tmp_path))

    with pytest.raises(ValidationError):
        handle.run_id = "other"


def test_file_read_result_preserves_content_and_serializes():
    result = FileReadResult(
        file="index.html",
        start_line=2,
        end_line=3,
        total_lines=5,
        content="  <h1>Hi</h1>\n  <p>There</p>\n",
        truncated=True,
    )

    restored = FileReadResult.model_validate_json(result.model_dump_json())

    assert restored.content == "  <h1>Hi</h1>\n  <p>There</p>\n"
    assert restored.start_line == 2


def test_file_read_result_rejects_inaccurate_range_metadata():
    with pytest.raises(ValidationError):
        FileReadResult(
            file="index.html",
            start_line=4,
            end_line=3,
            total_lines=5,
            content="",
        )
