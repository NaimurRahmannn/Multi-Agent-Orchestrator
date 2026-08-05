from agentorchestra.models import EditRequest
from agentorchestra.pipeline_models import EditRunReport, QARunResult
from agentorchestra.scripts import run_edit_flow
from tests.test_pipeline_models import manager_result, qa_result
from tests.test_qa_evidence import report as specialist_report
from tests.test_workspace_service import make_settings


class FakeFlow:
    def __init__(self, report):
        self.report = report
        self.calls = []

    def run(self, request):
        self.calls.append(request)
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
    if status == "accepted":
        return EditRunReport(
            request=request,
            status=status,
            manager_result=manager,
            plan=manager.plan,
            run_id=specialist.run_id,
            specialist_report=specialist,
            qa_run=qa,
            reviewed_diff=specialist.diff_report,
            final_diff=specialist.diff_report,
            working_updated=True,
            staging_cleaned=True,
            message="Flow message.",
            total_latency_ms=1.0,
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
    assert fake.calls == [EditRequest(target_page="index.html", instruction="Apply edit.")]
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
