from agentorchestra.config import get_settings
from agentorchestra.exceptions import ManagerExecutionError
from agentorchestra.models import EditRequest, ManagerRunResult, TokenUsage
from agentorchestra.scripts import run_manager, run_routing_benchmark


def plan_payload(status="execute", specialists=None):
    specialists = specialists or ["css"]
    if status == "clarification_required":
        return {
            "status": status,
            "request_type": "ambiguous_request",
            "selected_specialists": [],
            "routing_rationale": "The request is unclear.",
            "assignments": [],
            "acceptance_criteria": [],
            "clarification_question": "What should change?",
            "rejection_reason": None,
        }
    if status == "out_of_scope":
        return {
            "status": status,
            "request_type": "unsupported_backend",
            "selected_specialists": [],
            "routing_rationale": "Backend work is unsupported.",
            "assignments": [],
            "acceptance_criteria": [],
            "clarification_question": None,
            "rejection_reason": "Backend work is not supported.",
        }
    return {
        "status": status,
        "request_type": "seo_edit" if "seo" in specialists else "route",
        "selected_specialists": specialists,
        "routing_rationale": "Routed by ownership.",
        "assignments": [
            {"agent": specialist, "task": f"Handle {specialist} work."}
            for specialist in specialists
        ],
        "acceptance_criteria": ["Requested result is reflected."],
        "clarification_question": None,
        "rejection_reason": None,
    }


class FakeRouter:
    def __init__(self, output):
        self.output = output
        self.requests = []

    def route(self, request):
        self.requests.append(EditRequest.model_validate(request))
        if isinstance(self.output, Exception):
            raise self.output
        return ManagerRunResult(
            request=EditRequest.model_validate(request),
            plan=self.output,
            latency_ms=8.0,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            model="groq/test-model",
        )


def test_manager_cli_parses_args_and_prints_valid_output(capsys):
    router = FakeRouter(plan_payload())

    code = run_manager.main(
        ["--target-page", "index.html", "--instruction", "Change button color."],
        router=router,
    )

    output = capsys.readouterr().out
    assert code == 0
    assert router.requests[0].target_page == "index.html"
    assert "status: execute" in output
    assert "selected specialists: css" in output
    assert "unit-test-secret" not in output


def test_manager_cli_prints_clarification_and_out_of_scope(capsys):
    assert (
        run_manager.main(
            ["--target-page", "index.html", "--instruction", "Make it better"],
            router=FakeRouter(plan_payload(status="clarification_required")),
        )
        == 0
    )
    assert "clarification question:" in capsys.readouterr().out

    assert (
        run_manager.main(
            ["--target-page", "index.html", "--instruction", "Add backend validation"],
            router=FakeRouter(plan_payload(status="out_of_scope")),
        )
        == 0
    )
    assert "rejection reason:" in capsys.readouterr().out


def test_manager_cli_returns_nonzero_for_errors_and_redacts(capsys, monkeypatch):
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "unit-test-secret")
    get_settings.cache_clear()

    try:
        code = run_manager.main(
            ["--target-page", "index.html", "--instruction", "Change it"],
            router=FakeRouter(ManagerExecutionError("bad unit-test-secret")),
        )
    finally:
        get_settings.cache_clear()

    output = capsys.readouterr().out
    assert code == 1
    assert "Manager routing failed:" in output
    assert "unit-test-secret" not in output


def test_benchmark_cli_success_and_failure_exit_codes(tmp_path, capsys):
    success_router = FakeRouter(plan_payload())
    success_code = run_routing_benchmark.main(
        ["--report-path", str(tmp_path / "success.json")],
        router=success_router,
    )
    success_output = capsys.readouterr().out

    assert success_code == 1
    assert "report path:" in success_output

    class EightCaseRouter(FakeRouter):
        def __init__(self):
            self.requests = []

        def route(self, request):
            self.requests.append(EditRequest.model_validate(request))
            instruction = self.requests[-1].instruction
            if "heading bigger and add" in instruction:
                plan = plan_payload(specialists=["html", "css"])
            elif "broken <div>" in instruction or "alt text" in instruction:
                plan = plan_payload(specialists=["html"])
            elif "backend" in instruction:
                plan = plan_payload(status="out_of_scope")
            elif instruction == "Make it better":
                plan = plan_payload(status="clarification_required")
            elif "will not rank" in instruction:
                plan = plan_payload(specialists=["seo"])
            elif "meta description" in instruction:
                plan = plan_payload(specialists=["seo", "css"])
            else:
                plan = plan_payload(specialists=["css"])
            return ManagerRunResult(
                request=self.requests[-1],
                plan=plan,
                latency_ms=8.0,
                token_usage=TokenUsage(),
                model="groq/test-model",
            )

    good_path = tmp_path / "good.json"
    assert (
        run_routing_benchmark.main(["--report-path", str(good_path)], router=EightCaseRouter()) == 0
    )
    assert "correct routes: 8/8" in capsys.readouterr().out
    assert good_path.exists()
