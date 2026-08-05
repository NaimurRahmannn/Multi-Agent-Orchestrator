from agentorchestra.config import Settings
from agentorchestra.models import EditRequest, ManagerRunResult, TokenUsage
from agentorchestra.scripts import run_edit_preview
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from tests.specialist_helpers import execute_plan
from tests.test_specialist_cli import staged_runs
from tests.test_specialist_execution import ScriptedRunner
from tests.test_workspace_service import make_settings


def live_settings(tmp_path):
    base = make_settings(tmp_path)
    return Settings(
        project_root=base.project_root,
        groq_manager_api_key="manager-unit-test-secret",
        groq_html_api_key="html-unit-test-secret",
        groq_css_api_key="css-unit-test-secret",
        groq_manager_model="manager-test-model",
        groq_html_model="html-test-model",
        groq_css_model="css-test-model",
    )


class FakeRouter:
    def __init__(self, plan):
        self.plan = plan
        self.requests = []

    def route(self, request):
        self.requests.append(request)
        return ManagerRunResult(
            request=request,
            plan=self.plan,
            latency_ms=5.0,
            token_usage=TokenUsage(),
            model="groq/test-model",
        )


def service(settings, statuses):
    return SpecialistExecutionService(settings=settings, runner=ScriptedRunner(statuses))


def test_routed_preview_executes_html_css_and_combined_plans(tmp_path, capsys):
    for index, specialists in enumerate((("html",), ("css",), ("html", "css"))):
        settings = live_settings(tmp_path / str(index))
        plan = execute_plan(*specialists)
        router = FakeRouter(plan)
        code = run_edit_preview.main(
            ["--target-page", "index.html", "--instruction", "Apply routed edits."],
            settings=settings,
            router=router,
            execution_service=service(settings, ["succeeded"] * len(specialists)),
        )
        output = capsys.readouterr().out

        assert code == 0
        assert router.requests == [
            EditRequest(target_page="index.html", instruction="Apply routed edits.")
        ]
        assert "manager status: execute" in output
        assert f"manager selected specialists: {', '.join(specialists)}" in output
        assert "execution status: succeeded" in output
        assert "final unified diff:" in output
        assert "working unchanged: yes" in output
        assert "fixture unchanged: yes" in output
        assert staged_runs(settings) == []


def non_execute_plan(status):
    from agentorchestra.models import ManagerRoutingPlan

    return ManagerRoutingPlan(
        status=status,
        request_type="non_execute",
        selected_specialists=[],
        routing_rationale="The request cannot execute.",
        assignments=[],
        acceptance_criteria=[],
        clarification_question="What should change?" if status == "clarification_required" else None,
        rejection_reason="Unsupported request." if status == "out_of_scope" else None,
    )


def test_clarification_and_out_of_scope_create_no_staging(tmp_path, capsys):
    for index, status in enumerate(("clarification_required", "out_of_scope")):
        settings = live_settings(tmp_path / str(index))
        code = run_edit_preview.main(
            ["--target-page", "index.html", "--instruction", "Request."],
            settings=settings,
            router=FakeRouter(non_execute_plan(status)),
            execution_service=service(settings, []),
        )
        output = capsys.readouterr().out

        assert code == 2
        expected = "clarification question:" if status == "clarification_required" else "rejection reason:"
        assert expected in output
        assert staged_runs(settings) == []


def test_seo_plan_creates_no_staging_or_specialist_execution(tmp_path, capsys):
    settings = live_settings(tmp_path)
    code = run_edit_preview.main(
        ["--target-page", "index.html", "--instruction", "Improve SEO."],
        settings=settings,
        router=FakeRouter(execute_plan("seo")),
        execution_service=service(settings, []),
    )
    output = capsys.readouterr().out

    assert code == 3
    assert "SEO execution is not implemented yet" in output
    assert staged_runs(settings) == []


def test_specialist_failure_cleans_staging_and_returns_nonzero(tmp_path, capsys):
    settings = live_settings(tmp_path)
    code = run_edit_preview.main(
        ["--target-page", "index.html", "--instruction", "Apply CSS."],
        settings=settings,
        router=FakeRouter(execute_plan("css")),
        execution_service=service(settings, ["failed"]),
    )
    output = capsys.readouterr().out

    assert code == 1
    assert "execution status: failed" in output
    assert "staging cleanup: complete" in output
    assert "manager-unit-test-secret" not in output
    assert "css-unit-test-secret" not in output
    assert "qa" not in output.casefold()
    assert "promotion" not in output.casefold()
    assert staged_runs(settings) == []
