import pytest

from agentorchestra.models import (
    CriterionResult,
    ManagerRunResult,
    QAResult,
    TokenUsage,
)
from agentorchestra.pipeline_models import EditRunReport, QARunResult
from tests.specialist_helpers import execute_plan, request
from tests.test_qa_evidence import report


def qa_result(verdict="accept"):
    status = "passed" if verdict == "accept" else "failed"
    return QAResult(
        verdict=verdict,
        criteria_results=[
            CriterionResult(
                criterion="The requested staged edit is present.",
                status=status,
                evidence="Diff evidence.",
            )
        ],
        reason="QA reason.",
    )


def manager_result():
    plan = execute_plan("css")
    return ManagerRunResult(
        request=request(),
        plan=plan,
        latency_ms=1.0,
        token_usage=TokenUsage(),
        model="groq/test",
    )


def test_qa_run_result_round_trips_and_rejects_bad_latency():
    run = QARunResult(
        result=qa_result(),
        latency_ms=1.0,
        token_usage=TokenUsage(),
        model="groq/test",
        evidence_digest="a" * 64,
    )

    assert QARunResult.model_validate_json(run.model_dump_json()) == run
    with pytest.raises(ValueError):
        QARunResult(result=qa_result(), latency_ms=-1.0, model="groq/test")
    with pytest.raises(ValueError):
        QARunResult(result=qa_result(), latency_ms=1.0, model="")


def test_edit_run_report_accept_invariants():
    manager = manager_result()
    specialist_report = report(manager.plan)
    qa_run = QARunResult(result=qa_result(), latency_ms=1.0, model="groq/qa")

    accepted = EditRunReport(
        request=manager.request,
        status="accepted",
        manager_result=manager,
        plan=manager.plan,
        run_id=specialist_report.run_id,
        specialist_report=specialist_report,
        qa_run=qa_run,
        reviewed_diff=specialist_report.diff_report,
        final_diff=specialist_report.diff_report,
        working_updated=True,
        staging_cleaned=True,
        message="Accepted.",
        total_latency_ms=3.0,
    )

    assert accepted.status == "accepted"
    with pytest.raises(ValueError):
        EditRunReport(
            request=manager.request,
            status="accepted",
            manager_result=manager,
            plan=manager.plan,
            run_id=specialist_report.run_id,
            specialist_report=specialist_report,
            qa_run=QARunResult(result=qa_result("reject"), latency_ms=1.0, model="groq/qa"),
            reviewed_diff=specialist_report.diff_report,
            final_diff=specialist_report.diff_report,
            working_updated=True,
            staging_cleaned=True,
            message="Bad.",
            total_latency_ms=3.0,
        )


def test_edit_run_report_rejects_contradictory_normal_outcomes():
    manager = manager_result()
    specialist_report = report(manager.plan)

    with pytest.raises(ValueError):
        EditRunReport(
            request=manager.request,
            status="rejected",
            manager_result=manager,
            plan=manager.plan,
            specialist_report=specialist_report,
            qa_run=QARunResult(result=qa_result("reject"), latency_ms=1.0, model="groq/qa"),
            working_updated=True,
            staging_cleaned=True,
            message="Rejected.",
            total_latency_ms=3.0,
        )
    with pytest.raises(ValueError):
        EditRunReport(
            request=manager.request,
            status="clarification_required",
            manager_result=manager,
            plan=manager.plan,
            run_id="run",
            working_updated=False,
            staging_cleaned=True,
            message="Clarify.",
            total_latency_ms=1.0,
        )
    with pytest.raises(ValueError):
        EditRunReport(
            request=manager.request,
            status="failed",
            working_updated=False,
            staging_cleaned=True,
            message="Failed.",
            total_latency_ms=1.0,
        )
