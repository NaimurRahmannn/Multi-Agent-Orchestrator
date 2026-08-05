import pytest
from pydantic import ValidationError

from agentorchestra.specialist_models import (
    SpecialistCompletion,
    SpecialistExecutionReport,
    SpecialistRunResult,
)
from agentorchestra.workspace_models import DiffReport, FileDiff
from tests.specialist_helpers import (
    applied_patch,
    empty_diff,
    execute_plan,
    rejected_patch,
    request,
    run_result,
)


def edit_diff(*files: str) -> DiffReport:
    file_diffs = [
        FileDiff(
            file=file,
            unified_diff=f"--- working/{file}\n+++ staging/{file}\n-old\n+new\n",
            added_lines=1,
            removed_lines=1,
        )
        for file in sorted(files)
    ]
    return DiffReport(
        run_id="test-run",
        changed_files=sorted(files),
        files=file_diffs,
        combined_diff="".join(item.unified_diff for item in file_diffs),
        is_empty=False,
        total_added_lines=len(files),
        total_removed_lines=len(files),
    )


def test_valid_completed_and_blocked_completions():
    completed = SpecialistCompletion(status="completed", summary="Applied one HTML attribute.")
    blocked = SpecialistCompletion(
        status="blocked",
        summary="No safe exact target was available.",
        remaining_issue="The target text remained ambiguous.",
    )

    assert completed.remaining_issue is None
    assert blocked.remaining_issue


def test_completion_status_invariants_and_unknown_fields():
    with pytest.raises(ValidationError):
        SpecialistCompletion(status="completed", summary="Done.", remaining_issue="Still broken.")
    with pytest.raises(ValidationError):
        SpecialistCompletion(status="blocked", summary="Blocked.")
    with pytest.raises(ValidationError):
        SpecialistCompletion(status="completed", summary="Done.", raw_reasoning="secret")


def test_valid_succeeded_blocked_and_failed_runs():
    assert run_result(status="succeeded").status == "succeeded"
    assert run_result(status="blocked").status == "blocked"
    assert run_result(status="failed").status == "failed"


def test_succeeded_requires_applied_patch_and_completed_output():
    payload = run_result().model_dump()
    payload.update(
        patch_results=[], changed_files=[], applied_patch_count=0, rejected_patch_count=0
    )
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(payload)


def test_blocked_rejects_applied_patch_and_failed_requires_error():
    blocked = run_result(status="blocked").model_dump()
    blocked.update(
        patch_results=[applied_patch()],
        changed_files=["style.css"],
        applied_patch_count=1,
    )
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(blocked)

    failed = run_result(status="failed").model_dump()
    failed["error"] = None
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(failed)


def test_patch_counts_changed_files_latency_and_specialist_are_strict():
    payload = run_result(patches=[rejected_patch(), applied_patch()]).model_dump()
    payload["rejected_patch_count"] = 0
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(payload)

    payload = run_result().model_dump()
    payload["changed_files"] = ["style.css", "style.css"]
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(payload)

    payload = run_result().model_dump()
    payload["latency_ms"] = -0.1
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(payload)

    payload = run_result().model_dump()
    payload["specialist"] = "seo"
    with pytest.raises(ValidationError):
        SpecialistRunResult.model_validate(payload)


def test_run_and_report_json_round_trip():
    plan = execute_plan("css")
    result = run_result("css", assignment=plan.assignments[0].task)
    report = SpecialistExecutionReport(
        run_id="test-run",
        request=request(),
        plan=plan,
        status="succeeded",
        results=[result],
        diff_report=edit_diff("style.css"),
        total_latency_ms=10.0,
        stopped_early=False,
    )

    assert SpecialistRunResult.model_validate_json(result.model_dump_json()) == result
    assert SpecialistExecutionReport.model_validate_json(report.model_dump_json()) == report
    assert "staging_root" not in report.model_dump_json()


def test_execution_report_preserves_plan_order_and_status_invariants():
    plan = execute_plan("html", "css")
    html = run_result("html", assignment=plan.assignments[0].task)
    css = run_result("css", assignment=plan.assignments[1].task)
    valid = SpecialistExecutionReport(
        run_id="test-run",
        request=request(),
        plan=plan,
        status="succeeded",
        results=[html, css],
        diff_report=edit_diff("index.html", "style.css"),
        total_latency_ms=20.0,
        stopped_early=False,
    )
    assert [result.specialist.value for result in valid.results] == ["html", "css"]

    payload = valid.model_dump()
    payload["results"] = [css, html]
    with pytest.raises(ValidationError):
        SpecialistExecutionReport.model_validate(payload)
    payload = valid.model_dump()
    payload["status"] = "blocked"
    with pytest.raises(ValidationError):
        SpecialistExecutionReport.model_validate(payload)


def test_partial_report_requires_executed_prefix_and_stopped_early():
    plan = execute_plan("html", "css")
    blocked = run_result("html", "blocked", assignment=plan.assignments[0].task)
    report = SpecialistExecutionReport(
        run_id="test-run",
        request=request(),
        plan=plan,
        status="blocked",
        results=[blocked],
        diff_report=empty_diff(),
        total_latency_ms=10.0,
        stopped_early=True,
    )

    assert report.stopped_early is True
