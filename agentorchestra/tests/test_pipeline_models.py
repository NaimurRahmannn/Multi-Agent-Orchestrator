import pytest

from agentorchestra.models import (
    CriterionResult,
    ManagerRunResult,
    QAResult,
    TokenUsage,
)
from agentorchestra.pipeline_models import EditRunReport, PromotionResult, QARunResult
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


def promotion_result(specialist_report, *, warning=False):
    digest = "d" * 64
    warnings = ["Could not remove obsolete backup 'backup'."] if warning else []
    return PromotionResult(
        run_id=specialist_report.run_id,
        status="committed_with_warning" if warning else "committed",
        working_updated=True,
        reviewed_diff=specialist_report.diff_report,
        final_diff=specialist_report.diff_report,
        staging_cleaned=True,
        candidate_cleaned=True,
        backup_cleaned=not warning,
        accepted_content_digest=digest,
        final_working_digest=digest,
        warnings=warnings,
        message="Committed.",
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
    promotion = promotion_result(specialist_report)

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
        promotion_result=promotion,
        promotion_status=promotion.status,
        accepted_content_digest=promotion.accepted_content_digest,
        final_working_digest=promotion.final_working_digest,
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
            promotion_result=promotion,
            promotion_status=promotion.status,
            accepted_content_digest=promotion.accepted_content_digest,
            final_working_digest=promotion.final_working_digest,
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


def test_edit_run_report_accepts_committed_cleanup_warning_honestly():
    manager = manager_result()
    specialist_report = report(manager.plan)
    qa_run = QARunResult(result=qa_result(), latency_ms=1.0, model="groq/qa")
    promotion = promotion_result(specialist_report, warning=True)

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
        promotion_result=promotion,
        promotion_status=promotion.status,
        accepted_content_digest=promotion.accepted_content_digest,
        final_working_digest=promotion.final_working_digest,
        working_updated=True,
        staging_cleaned=True,
        message="Accepted with warning.",
        total_latency_ms=3.0,
        warnings=promotion.warnings,
        cleanup_warnings=promotion.warnings,
    )

    assert accepted.status == "accepted"
    assert accepted.promotion_status == "committed_with_warning"
    assert accepted.error is None


def test_edit_run_report_rejects_mismatched_committed_digests():
    manager = manager_result()
    specialist_report = report(manager.plan)
    promotion = promotion_result(specialist_report)

    with pytest.raises(ValueError):
        EditRunReport(
            request=manager.request,
            status="accepted",
            manager_result=manager,
            plan=manager.plan,
            run_id=specialist_report.run_id,
            specialist_report=specialist_report,
            qa_run=QARunResult(result=qa_result(), latency_ms=1.0, model="groq/qa"),
            reviewed_diff=specialist_report.diff_report,
            final_diff=specialist_report.diff_report,
            promotion_result=promotion,
            promotion_status=promotion.status,
            accepted_content_digest="e" * 64,
            final_working_digest="f" * 64,
            working_updated=True,
            staging_cleaned=True,
            message="Contradictory.",
            total_latency_ms=3.0,
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
