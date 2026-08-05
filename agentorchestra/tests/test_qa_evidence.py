import pytest

from agentorchestra.exceptions import ExecutionEvidenceError
from agentorchestra.models import EditRequest
from agentorchestra.services.qa_evidence import (
    build_qa_evidence_bundle,
    validate_execution_evidence,
)
from agentorchestra.specialist_models import SpecialistExecutionReport
from agentorchestra.workspace_models import DiffReport, FileDiff
from tests.specialist_helpers import applied_patch, execute_plan, run_result


def diff(run_id="qa-run", files=("style.css",)):
    return DiffReport(
        run_id=run_id,
        changed_files=list(files),
        files=[
            FileDiff(
                file=file,
                unified_diff=f"--- working/{file}\n+++ staging/{file}\n- old\n+ new\n",
                added_lines=1,
                removed_lines=1,
            )
            for file in files
        ],
        combined_diff="".join(
            f"--- working/{file}\n+++ staging/{file}\n- old\n+ new\n" for file in files
        ),
        is_empty=False,
        total_added_lines=len(files),
        total_removed_lines=len(files),
    )


def report(plan=None, diff_report=None):
    plan = plan or execute_plan("css")
    diff_report = diff_report or diff()
    return SpecialistExecutionReport(
        run_id=diff_report.run_id,
        request=EditRequest(target_page="index.html", instruction="Change CSS."),
        plan=plan,
        status="succeeded",
        results=[
            run_result("css", assignment=plan.assignments[0].task, patches=[applied_patch()])
        ],
        diff_report=diff_report,
        total_latency_ms=10.0,
        stopped_early=False,
    )


def test_qa_evidence_bundle_is_deterministic_and_safe():
    plan = execute_plan("css")
    specialist_report = report(plan)

    first = build_qa_evidence_bundle(
        request=specialist_report.request,
        plan=plan,
        specialist_report=specialist_report,
        diff_report=specialist_report.diff_report,
    )
    second = build_qa_evidence_bundle(
        request=specialist_report.request,
        plan=plan,
        specialist_report=specialist_report,
        diff_report=specialist_report.diff_report,
    )

    assert first == second
    assert first.evidence_digest == second.evidence_digest
    assert first.acceptance_criteria == plan.acceptance_criteria
    assert first.changed_files == ["style.css"]
    assert first.specialist_results[0].patch_results[0].before_sha256 == "a" * 64
    dumped = first.model_dump_json()
    assert "GROQ" not in dumped
    assert ":\\\\" not in dumped


def test_qa_evidence_digest_changes_with_diff():
    plan = execute_plan("css")
    first_report = report(plan, diff("qa-run", ("style.css",)))
    changed_report = report(plan, diff("qa-run", ("index.html",)))
    changed_report.results = [
        run_result("css", assignment=plan.assignments[0].task, patches=[applied_patch("index.html")])
    ]

    first = build_qa_evidence_bundle(
        request=first_report.request,
        plan=plan,
        specialist_report=first_report,
        diff_report=first_report.diff_report,
    )

    with pytest.raises(ExecutionEvidenceError):
        build_qa_evidence_bundle(
            request=changed_report.request,
            plan=plan,
            specialist_report=changed_report,
            diff_report=changed_report.diff_report,
        )
    assert first.evidence_digest


def test_validate_execution_evidence_rejects_mismatched_patch_and_diff():
    plan = execute_plan("css")
    specialist_report = report(plan, diff("qa-run", ("index.html",)))

    with pytest.raises(ExecutionEvidenceError):
        validate_execution_evidence(plan, specialist_report, specialist_report.diff_report)


def test_validate_execution_evidence_rejects_empty_diff():
    plan = execute_plan("css")
    empty = DiffReport(run_id="qa-run")
    specialist_report = report(plan, empty)

    with pytest.raises(ExecutionEvidenceError):
        validate_execution_evidence(plan, specialist_report, empty)
