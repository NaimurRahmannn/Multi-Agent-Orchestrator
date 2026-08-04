import json
from datetime import UTC, datetime

from agentorchestra.evaluation.routing_runner import (
    RoutingBenchmarkRunner,
    write_routing_benchmark_report,
)
from agentorchestra.models import EditRequest, ManagerRunResult, RoutingEvidenceCase, TokenUsage


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
        "request_type": "route",
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


def case(case_id="case", status="execute", specialists=None):
    return RoutingEvidenceCase(
        case_id=case_id,
        request="Change button color.",
        expected_status=status,
        expected_specialists=specialists or (["css"] if status == "execute" else []),
    )


class FakeRouter:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    def route(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ManagerRunResult(
            request=EditRequest.model_validate(request),
            plan=output,
            latency_ms=12.0,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            model="groq/test-model",
        )


def fixed_now():
    return datetime(2026, 8, 4, 9, 0, tzinfo=UTC)


def test_all_correct_benchmark_summary_and_requests():
    runner = RoutingBenchmarkRunner(
        FakeRouter([plan_payload()]),
        target_page_resolver=lambda routing_case: "index.html",
        now_provider=fixed_now,
    )

    report = runner.run([case()])

    assert report.total_cases == 1
    assert report.structurally_valid_cases == 1
    assert report.correct_cases == 1
    assert report.routing_accuracy == "1.0000"
    assert report.generated_at == "2026-08-04T09:00:00Z"


def test_incorrect_specialist_route_and_order_insensitive_match():
    cases = [case("wrong", specialists=["html"]), case("right", specialists=["html", "css"])]
    runner = RoutingBenchmarkRunner(
        FakeRouter([plan_payload(specialists=["css"]), plan_payload(specialists=["css", "html"])]),
        now_provider=fixed_now,
    )

    report = runner.run(cases)

    assert [result.routing_correct for result in report.results] == [False, True]
    assert report.correct_cases == 1
    assert [specialist.value for specialist in report.results[1].actual_specialists] == ["css", "html"]


def test_incorrect_status_is_counted():
    report = RoutingBenchmarkRunner(
        FakeRouter([plan_payload(status="out_of_scope")]),
        now_provider=fixed_now,
    ).run([case()])

    assert report.structurally_valid_cases == 1
    assert report.correct_cases == 0


def test_structural_validation_failure_and_execution_exception_continue():
    cases = [case("bad"), case("throws"), case("good")]
    report = RoutingBenchmarkRunner(
        FakeRouter([ValueError("invalid output"), RuntimeError("provider down"), plan_payload()]),
        now_provider=fixed_now,
    ).run(cases)

    assert report.total_cases == 3
    assert report.structurally_valid_cases == 1
    assert report.correct_cases == 1
    assert report.results[0].validation_error
    assert report.results[1].validation_error
    assert report.results[2].structurally_valid


def test_benchmark_paces_calls_after_the_first_case():
    delays = []
    runner = RoutingBenchmarkRunner(
        FakeRouter([plan_payload(), plan_payload(), plan_payload()]),
        now_provider=fixed_now,
        delay_seconds=1.25,
        sleeper=delays.append,
    )

    runner.run([case("one"), case("two"), case("three")])

    assert delays == [1.25, 1.25]


def test_report_json_serialization_is_deterministic_and_safe(tmp_path):
    report = RoutingBenchmarkRunner(
        FakeRouter([plan_payload()]),
        now_provider=fixed_now,
    ).run([case()])
    path = write_routing_benchmark_report(report, tmp_path / "report.json")

    first = path.read_text(encoding="utf-8")
    write_routing_benchmark_report(report, path)
    second = path.read_text(encoding="utf-8")
    payload = json.loads(first)

    assert first == second
    assert payload["routing_accuracy"] == "1.0000"
    assert "api_key" not in first
    assert "provider_response" not in first
