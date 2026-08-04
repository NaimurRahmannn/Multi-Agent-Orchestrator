import pytest
from pydantic import ValidationError

from agentorchestra.workspace_models import (
    DiffReport,
    FileDiff,
    FileReadResult,
    PatchExecutionResult,
    PatchRejectionReason,
    PatchStatus,
    WorkspaceHandle,
    WorkspaceLimits,
)


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


def test_workspace_limits_are_immutable_and_validate_positive_values():
    limits = WorkspaceLimits(max_file_bytes=10)

    with pytest.raises(ValidationError):
        limits.max_file_bytes = 20
    with pytest.raises(ValidationError):
        WorkspaceLimits(max_file_bytes=0)


def test_applied_patch_result_requires_and_round_trips_rich_evidence():
    result = PatchExecutionResult(
        status=PatchStatus.APPLIED,
        file="style.css",
        specialist="css",
        summary="Change color.",
        match_count=1,
        replacements=1,
        bytes_before=10,
        bytes_after=12,
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        rejection_reason=None,
        message="Patch applied atomically.",
    )

    restored = PatchExecutionResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.before_sha256 != restored.after_sha256
    assert restored.rejection_reason is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"match_count": 2},
        {"replacements": 0},
        {"after_sha256": "a" * 64},
        {"rejection_reason": PatchRejectionReason.NO_OP},
    ],
)
def test_applied_patch_result_rejects_inconsistent_evidence(overrides):
    payload = {
        "status": PatchStatus.APPLIED,
        "file": "style.css",
        "specialist": "css",
        "summary": "Change color.",
        "match_count": 1,
        "replacements": 1,
        "bytes_before": 10,
        "bytes_after": 12,
        "before_sha256": "a" * 64,
        "after_sha256": "b" * 64,
        "rejection_reason": None,
        "message": "Patch applied atomically.",
    }
    payload.update(overrides)

    with pytest.raises(ValidationError):
        PatchExecutionResult(**payload)


def test_rejected_patch_result_cannot_claim_a_write():
    result = PatchExecutionResult(
        status=PatchStatus.REJECTED,
        file="style.css",
        specialist="css",
        summary="Missing target.",
        match_count=0,
        replacements=0,
        bytes_before=10,
        bytes_after=10,
        before_sha256="a" * 64,
        after_sha256="a" * 64,
        rejection_reason=PatchRejectionReason.TARGET_NOT_FOUND,
        message="old_text was not found.",
    )

    assert PatchExecutionResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        PatchExecutionResult(**{**result.model_dump(), "after_sha256": "b" * 64})


def test_diff_report_validates_per_file_totals_and_json_round_trip():
    file_diff = FileDiff(
        file="style.css",
        unified_diff="--- working/style.css\n+++ staging/style.css\n",
        added_lines=1,
        removed_lines=1,
    )
    report = DiffReport(
        run_id="diff-run",
        changed_files=["style.css"],
        files=[file_diff],
        combined_diff=file_diff.unified_diff,
        is_empty=False,
        total_added_lines=1,
        total_removed_lines=1,
    )

    restored = DiffReport.model_validate_json(report.model_dump_json())

    assert restored == report
    assert restored.unified_diff == restored.combined_diff
