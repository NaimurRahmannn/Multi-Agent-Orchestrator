from agentorchestra.exceptions import PromotionRollbackError
from agentorchestra.models import EditRequest
from agentorchestra.pipeline_models import (
    EditRunReport,
    PromotionResult,
    QARunResult,
)
from agentorchestra.scripts import run_edit_flow
from tests.test_pipeline_models import manager_result, qa_result
from tests.test_qa_evidence import report as specialist_report
from tests.test_workspace_service import make_settings


class FakeFlow:
    def __init__(self, report):
        self.report = report
        self.calls = []

    def kickoff(self, *, inputs):
        self.calls.append(inputs)
        return self.report


def report(status="accepted"):
    manager = manager_result()
    specialist = specialist_report(manager.plan)
    qa = QARunResult(
        result=qa_result("reject" if status == "rejected" else "accept"),
        latency_ms=1.0,
        model="groq/qa",
    )
    request = manager.request
    if status in {"accepted", "accepted_warning"}:
        digest = "d" * 64
        warning = status == "accepted_warning"
        warnings = ["Could not remove staged run 'test-run'."] if warning else []
        promotion = PromotionResult(
            run_id=specialist.run_id,
            status="committed_with_warning" if warning else "committed",
            working_updated=True,
            reviewed_diff=specialist.diff_report,
            final_diff=specialist.diff_report,
            staging_cleaned=not warning,
            candidate_cleaned=True,
            backup_cleaned=True,
            accepted_content_digest=digest,
            final_working_digest=digest,
            warnings=warnings,
            message="Committed.",
        )
        return EditRunReport(
            request=request,
            status="accepted",
            manager_result=manager,
            plan=manager.plan,
            run_id=specialist.run_id,
            specialist_report=specialist,
            qa_run=qa,
            reviewed_diff=specialist.diff_report,
            final_diff=specialist.diff_report,
            promotion_result=promotion,
            promotion_status=promotion.status,
            accepted_content_digest=digest,
            final_working_digest=digest,
            working_updated=True,
            staging_cleaned=not warning,
            message="Flow message.",
            total_latency_ms=1.0,
            warnings=warnings,
            cleanup_warnings=warnings,
        )
    if status == "rejected":
        return EditRunReport(
            request=request,
            status=status,
            manager_result=manager,
            plan=manager.plan,
            specialist_report=specialist,
            qa_run=qa,
            working_updated=False,
            staging_cleaned=True,
            message="Flow message.",
            total_latency_ms=1.0,
        )
    if status == "blocked":
        return EditRunReport(
            request=request,
            status=status,
            manager_result=manager,
            plan=manager.plan,
            specialist_report=specialist,
            working_updated=False,
            staging_cleaned=True,
            message="Flow message.",
            total_latency_ms=1.0,
        )
    return EditRunReport(
        request=request,
        status=status,
        working_updated=status == "accepted",
        staging_cleaned=True,
        message="Flow message.",
        error="Internal failure." if status == "failed" else None,
        total_latency_ms=1.0,
    )


def test_edit_flow_cli_requires_apply_before_flow_call(tmp_path, capsys):
    settings = make_settings(tmp_path)
    fake = FakeFlow(report())

    code = run_edit_flow.main(
        ["--target-page", "index.html", "--instruction", "Apply edit."],
        settings=settings,
        flow=fake,
    )
    output = capsys.readouterr().out

    assert code == 2
    assert fake.calls == []
    assert "--apply" in output


def test_edit_flow_cli_prints_accepted_report(tmp_path, capsys):
    settings = make_settings(tmp_path)
    fake = FakeFlow(report("accepted"))

    code = run_edit_flow.main(
        ["--target-page", "index.html", "--instruction", "Apply edit.", "--apply"],
        settings=settings,
        flow=fake,
    )
    output = capsys.readouterr().out

    assert code == 0
    assert fake.calls == [
        {
            "request": EditRequest(target_page="index.html", instruction="Apply edit.").model_dump(
                mode="json"
            )
        }
    ]
    assert "flow outcome: accepted" in output
    assert "working updated: yes" in output


def test_edit_flow_cli_uses_stable_nonzero_status_codes(tmp_path, capsys):
    settings = make_settings(tmp_path)

    for status, expected in [
        ("rejected", 4),
        ("clarification_required", 5),
        ("out_of_scope", 6),
        ("unsupported_specialist", 7),
        ("blocked", 8),
        ("failed", 1),
    ]:
        code = run_edit_flow.main(
            ["--target-page", "index.html", "--instruction", "Apply edit.", "--apply"],
            settings=settings,
            flow=FakeFlow(report(status)),
        )
        output = capsys.readouterr().out

        assert code == expected
        assert "flow outcome:" in output


def test_edit_flow_cli_reports_committed_cleanup_warning(tmp_path, capsys):
    settings = make_settings(tmp_path)

    code = run_edit_flow.main(
        ["--target-page", "index.html", "--instruction", "Apply edit.", "--apply"],
        settings=settings,
        flow=FakeFlow(report("accepted_warning")),
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "flow outcome: accepted" in output
    assert "promotion status: committed_with_warning" in output
    assert "cleanup warning:" in output


def test_edit_flow_cli_uses_distinct_critical_recovery_exit_code(tmp_path, capsys):
    settings = make_settings(tmp_path)

    class CriticalFlow:
        def kickoff(self, *, inputs):
            del inputs
            raise PromotionRollbackError(
                "Recovery required.",
                recovery_paths=("working", ".agentorchestra-backup-test"),
            )

    code = run_edit_flow.main(
        ["--target-page", "index.html", "--instruction", "Apply edit.", "--apply"],
        settings=settings,
        flow=CriticalFlow(),
    )
    output = capsys.readouterr().out

    assert code == run_edit_flow.CRITICAL_RECOVERY_EXIT_CODE == 9
    assert "Critical: working-site recovery is required" in output
    assert str(tmp_path) not in output


def test_edit_flow_cli_prints_seo_edit_audit_patch_and_qa_evidence(tmp_path, capsys):
    from tests.test_seo_flow import run_flow

    settings, _, seo_report = run_flow(tmp_path / "flow", verdict="accept")
    code = run_edit_flow.main(
        ["--target-page", "index.html", "--instruction", "Improve SEO.", "--apply"],
        settings=settings,
        flow=FakeFlow(seo_report),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "manager selected specialists: seo" in output
    assert "specialist seo patch 1: status=applied file=index.html" in output
    assert "lighthouse seo score: 90" in output
    assert "lighthouse failed audits: none" in output
    assert "qa verdict: accept" in output
    assert "working updated: yes" in output


def test_edit_flow_cli_prints_diagnostic_without_qa_or_update(tmp_path, capsys):
    from tests.test_seo_flow import run_flow

    settings, _, diagnostic = run_flow(tmp_path / "flow", diagnostic=True)
    code = run_edit_flow.main(
        ["--target-page", "index.html", "--instruction", "Diagnose SEO.", "--apply"],
        settings=settings,
        flow=FakeFlow(diagnostic),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "flow outcome: diagnostic_completed" in output
    assert "seo finding [warning] missing_description" in output
    assert "lighthouse seo score: 90" in output
    assert "qa run: no" in output
    assert "working updated: no" in output
    assert "staging cleaned: yes" in output
